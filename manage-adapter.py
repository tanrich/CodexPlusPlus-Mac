#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tanrich
"""CodexPlusPlus-Mac: manage a version-checked external browser adapter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

VERSION = '0.1.0'
BASE = Path(__file__).resolve().parent
SERVICE = 'lib/node_modules/@oai/browser-desktop/scripts/browser-service.mjs'
ORIGINAL = '@oai/browser-desktop/service'
OVERLAY_DIRS = (
    'lib/node_modules', 'lib/node_modules/@oai',
    'lib/node_modules/@oai/browser-desktop',
    'lib/node_modules/@oai/browser-desktop/scripts',
)
WRAPPER = (
    'export { handleRpc } from '
    '"./lib/node_modules/@oai/browser-desktop/scripts/browser-service.mjs";\n'
).encode()


class AdapterError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AdapterError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path, limit=128 * 1024 * 1024):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), '文件缺失或为链接：' + str(path))
    require(path.stat().st_size <= limit, '文件超过允许大小：' + str(path))
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, '文件读取期间大小发生变化：' + str(path))
    return value


def load_json(path):
    return json.loads(read_file(path))


def encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def write_new(path, value, mode=0o600):
    path = Path(path)
    with path.open('xb') as stream:
        stream.write(value)
    path.chmod(mode)


def replace_file(path, value, previous):
    """Atomic replacement; never overwrite an observed concurrent change."""
    path = Path(path)
    require(read_file(path) == previous, '文件被其他程序更改，请重试：' + str(path))
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        require(read_file(path) == previous, '写入前文件已变化，请重试：' + str(path))
        os.replace(temporary, path)
        require(read_file(path) == value, '写入后校验失败：' + str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def settings(descriptor):
    document = json.loads(read_file(descriptor))
    server = document['mcpServers']['cua_repl']
    env = server['env']
    services = json.loads(env['NODE_REPL_TRUSTED_SERVICES'])
    require(isinstance(services, dict) and isinstance(services.get('browser'), str),
            '浏览器服务配置无效')
    return document, server, env, services


def locate_app(explicit=None):
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        candidates = [Path('/Applications/ChatGPT.app'), Path('/Applications/Codex.app'),
                      Path.home() / 'Applications/ChatGPT.app', Path.home() / 'Applications/Codex.app']
    found = [p.resolve() for p in candidates if (p / 'Contents/Resources/cua_node/manifest.json').is_file()]
    require(len(found) == 1, '无法唯一定位 App；请用 --app 指定 Codex/ChatGPT .app 路径')
    return found[0]


def locate_descriptor(codex_home, explicit=None):
    cache = codex_home / 'plugins/cache'
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        candidates = list(cache.glob('*/unified-computer-use/*/.mcp.json'))
    found = []
    for path in candidates:
        require(not path.is_symlink(), '插件描述符不能是符号链接：' + str(path))
        if not path.is_file():
            continue
        resolved = path.resolve()
        require(resolved.is_relative_to(cache.resolve()), '描述符必须位于当前 CODEX_HOME/plugins/cache')
        try:
            settings(resolved)
        except (KeyError, ValueError, AdapterError):
            continue
        found.append(resolved)
    require(len(found) == 1, '无法唯一定位插件配置；启动 App 后，用 --descriptor 指定当前 .mcp.json')
    return found[0]


def validate_runtime(runtime, assets=BASE):
    profile = load_json(assets / 'assets/runtime-profile.json')
    require(profile.get('schema') == 1, '不支持的版本校验文件')
    require(profile.get('platform') == 'darwin' and profile.get('arch') == 'arm64', '仅支持 macOS Apple Silicon')
    for relative, expected in profile['files'].items():
        require((runtime / relative).resolve().is_relative_to(runtime.resolve()), 'runtime 文件越界')
        require(sha(read_file(runtime / relative)) == expected, '不支持当前 App 版本，文件校验未通过：' + relative)
    manifest = load_json(runtime / 'manifest.json')
    require(manifest.get('platform') == 'darwin' and manifest.get('arch') == 'arm64'
            and manifest.get('target') == 'darwin-arm64'
            and manifest.get('runtime_archive_version') == profile['runtime_archive_version'],
            'App runtime 平台或版本不匹配')
    helper = read_file(assets / 'vendor/require-identification.mjs')
    require(sha(helper) == profile['helper_sha256'], '上游辅助函数已变化')
    source = read_file(runtime / SERVICE)
    binding = profile['binding']
    start, end = binding['start'], binding['end']
    require(binding['policy'] == 'NO' and binding['metadata'] == 'qe'
            and source[start:end] == b'NO' and b'cppNativeIdentificationReader' not in source,
            '已验证的回调位置不匹配')
    return profile, source, helper


def candidate_bytes(source, helper, profile, deployment):
    binding = profile['binding']
    start, end = binding['start'], binding['end']
    callback = ('cppNativeIdentificationReader(this.runtime,NO,qe,'
                + json.dumps(str(deployment / 'control.json')) + ')').encode()
    return source[:start] + callback + source[end:] + b'\n' + helper


def syntax_check(runtime, service):
    result = subprocess.run([str(runtime / 'bin/node'), '--no-addons', '--check', str(service)],
                            capture_output=True, text=True, timeout=30,
                            env={'PATH': '/usr/bin:/bin'})
    require(result.returncode == 0, '候选服务语法校验失败：' + result.stderr[:400])


def install(root, codex_home, app, descriptor, assets=BASE):
    require(not root.exists() and not root.is_symlink(), '已存在安装目录，请先使用 status 检查；不会覆盖现有安装')
    runtime = app / 'Contents/Resources/cua_node'
    profile, source, helper = validate_runtime(runtime, assets)
    original_bytes = read_file(descriptor)
    _, _, _, services = settings(descriptor)
    require(services['browser'] == ORIGINAL, '已有其他浏览器适配，请先恢复其官方映射；本工具不会覆盖它')
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.codexplusplus-mac-', dir=root.parent))
    deployment = root / 'deployment'
    try:
        for directory in OVERLAY_DIRS:
            output = stage / 'deployment' / directory
            output.mkdir(parents=True, mode=0o700, exist_ok=True)
            for child in (runtime / directory).iterdir():
                relative = str(child.relative_to(runtime))
                if relative not in OVERLAY_DIRS and relative != SERVICE:
                    (output / child.name).symlink_to(child, target_is_directory=child.is_dir())
        candidate = candidate_bytes(source, helper, profile, deployment)
        write_new(stage / 'deployment' / SERVICE, candidate)
        write_new(stage / 'deployment/control.json', encode({'schema': 1, 'requireIdentification': False}))
        write_new(stage / 'deployment/service-wrapper.mjs', WRAPPER)
        syntax_check(runtime, stage / 'deployment' / SERVICE)
        write_new(stage / 'descriptor.before-install.json', original_bytes)
        # Install sources and small redistributable assets only; no App bundle is published.
        (stage / 'bin/assets').mkdir(parents=True, mode=0o700)
        (stage / 'bin/vendor').mkdir(mode=0o700)
        write_new(stage / 'bin/manage-adapter.py', read_file(assets / 'manage-adapter.py'), mode=0o700)
        for relative in ['assets/runtime-profile.json', 'vendor/require-identification.mjs',
                         'LICENSE', 'THIRD_PARTY_NOTICES.md']:
            write_new(stage / 'bin' / relative, read_file(assets / relative))
        metadata = {
            'schema': 1, 'version': VERSION, 'codex_home': str(codex_home), 'runtime': str(runtime),
            'descriptor': str(descriptor), 'profile_sha256': sha(read_file(assets / 'assets/runtime-profile.json')),
            'backup_sha256': sha(original_bytes), 'service_sha256': sha(candidate),
        }
        write_new(stage / 'installation.json', encode(metadata))
        # Recheck installed App and target config after generation, before publishing the local directory.
        validate_runtime(runtime, assets)
        require(read_file(descriptor) == original_bytes, '安装期间插件配置发生变化，请重试')
        require(not root.exists(), '安装目录已被其他程序创建，请重试')
        stage.rename(root)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return metadata


def installed_record(root, codex_home):
    record = load_json(root / 'installation.json')
    require(record.get('schema') == 1 and record['codex_home'] == str(codex_home), '安装记录与 CODEX_HOME 不匹配')
    descriptor = Path(record['descriptor'])
    require(not descriptor.is_symlink() and descriptor.resolve().is_relative_to((codex_home / 'plugins/cache').resolve()),
            '插件配置不在当前 CODEX_HOME 中')
    return record


def verify_install(root, record):
    assets = root / 'bin'
    require(sha(read_file(assets / 'assets/runtime-profile.json')) == record['profile_sha256'], '版本校验文件已变化')
    profile, source, helper = validate_runtime(Path(record['runtime']), assets)
    deployment = root / 'deployment'
    expected = candidate_bytes(source, helper, profile, deployment)
    require(read_file(deployment / SERVICE) == expected and sha(expected) == record['service_sha256'],
            '部署服务不符合已验证补丁')
    require(read_file(deployment / 'service-wrapper.mjs') == WRAPPER, '服务入口已变化')
    require(sha(read_file(root / 'descriptor.before-install.json')) == record['backup_sha256'], '原始备份已变化')


def trusted_location(deployment, env):
    roots = [Path(value).resolve() for value in env.get('NODE_REPL_TRUSTED_CODE_PATHS', '').split(os.pathsep) if value]
    require(any(deployment.resolve().is_relative_to(root) for root in roots), '部署目录不在现有信任根中；不会扩大信任范围')


def manage(root, codex_home, action):
    record = installed_record(root, codex_home)
    descriptor = Path(record['descriptor'])
    previous = read_file(descriptor)
    document, server, env, services = settings(descriptor)
    deployment = root / 'deployment'
    wrapper = str(deployment / 'service-wrapper.mjs')
    require(services['browser'] in [ORIGINAL, wrapper], 'browser 已指向其他适配器，已停止')
    control_path = deployment / 'control.json'
    control_before = read_file(control_path)
    control = json.loads(control_before)
    require(control.get('schema') == 1 and type(control.get('requireIdentification')) is bool, 'control 格式无效')
    valid, reason = True, None
    try:
        verify_install(root, record)
        require(server.get('enabled') is not False, '浏览器工具配置为关闭，请在 App 启用浏览器工具')
        trusted_location(deployment, env)
    except (AdapterError, OSError, KeyError, ValueError) as error:
        valid, reason = False, str(error)
    if action == 'enable':
        require(valid, reason)
    if action != 'status':
        enabled = action == 'enable'
        services['browser'] = wrapper if enabled else ORIGINAL
        # No other descriptor or service field is changed, including sky and trust roots.
        if services != json.loads(env['NODE_REPL_TRUSTED_SERVICES']):
            env['NODE_REPL_TRUSTED_SERVICES'] = json.dumps(services, separators=(',', ':'))
        new_descriptor = encode(document)
        if json.loads(previous) == document:
            new_descriptor = previous
        elif not enabled:
            backup = read_file(root / 'descriptor.before-install.json')
            require(sha(backup) == record['backup_sha256'], '原始备份已变化，无法回滚')
            backup_document = json.loads(backup)
            backup_mapping = backup_document['mcpServers']['cua_repl']['env']['NODE_REPL_TRUSTED_SERVICES']
            if json.loads(backup_mapping) == services:
                env['NODE_REPL_TRUSTED_SERVICES'] = backup_mapping
                new_descriptor = encode(document)
            if backup_document == document:
                new_descriptor = backup
        control['requireIdentification'] = enabled
        new_control = encode(control) if json.loads(control_before) != control else control_before
        # Two-file update with rollback of our own change if the second write fails.
        if new_descriptor != previous:
            replace_file(descriptor, new_descriptor, previous)
        try:
            if new_control != control_before:
                replace_file(control_path, new_control, control_before)
        except (AdapterError, OSError):
            if new_descriptor != previous:
                replace_file(descriptor, previous, new_descriptor)
            raise
    return {
        'action': action, 'installed': True, 'runtime_verified': valid, 'verification_error': reason,
        'browser_mapping': 'adapter' if services['browser'] == wrapper else 'official',
        'requireIdentification': control['requireIdentification'], 'browser_connection': 'not_tested',
        'existing_connections_reloaded': False, 'auth_files_changed': False, 'app_files_changed': False,
    }


def add_alias(script, zshrc, codex_home=None):
    previous = read_file(zshrc) if zshrc.exists() else b''
    command = 'python3 ' + shlex.quote(str(script))
    if codex_home is not None:
        command += ' --codex-home ' + shlex.quote(str(codex_home))
    quoted = shlex.quote(command)
    line = 'alias manage-adapter=' + quoted
    existing = [s.strip() for s in previous.decode().splitlines() if s.strip().startswith('alias manage-adapter=')]
    require(not existing or existing == [line], '已存在其他 manage-adapter alias，请先手动核对 ~/.zshrc')
    if existing:
        return False
    backup = script.parent.parent / 'zshrc.before-alias'
    if not backup.exists():
        write_new(backup, previous)
    block = ('\n# >>> CodexPlusPlus-Mac\n' + line + '\n# <<< CodexPlusPlus-Mac\n').encode()
    if zshrc.exists():
        replace_file(zshrc, previous + block, previous)
    else:
        write_new(zshrc, block)
    return True


def print_result(result):
    action = result['action']
    if action in ['install', 'alias']:
        print('安装完成，适配尚未启用。' if action == 'install' else 'zsh 快捷命令已配置。')
        print('命令路径：' + result['command'])
        if action == 'alias':
            print('新开终端即可使用 manage-adapter；已有终端执行 source ~/.zshrc。')
        else:
            print('下一步：运行上述脚本的 alias 命令，再执行 manage-adapter enable。')
        return
    if action == 'check':
        print('当前 App 的文件校验通过：' + result['runtime'])
        print('浏览器连接：未检测（此命令只检查本地文件）')
        return
    if not result['installed']:
        print('适配状态：尚未安装')
        print('下一步：python3 manage-adapter.py install')
        return
    mapped, enabled = result['browser_mapping'] == 'adapter', result['requireIdentification']
    if mapped and enabled:
        state = '已启用（配置已写入）'
    elif not mapped and not enabled:
        state = '已关闭'
    else:
        state = '配置不一致，请执行 enable 或 restore'
    print('适配状态：' + state)
    print('服务配置：' + ('本地适配服务' if mapped else '官方浏览器服务'))
    print('文件校验：' + ('通过' if result['runtime_verified'] else '未通过'))
    if result['verification_error']:
        print('校验原因：' + result['verification_error'])
    print('浏览器连接：未检测（此命令只检查本地配置）')
    if action == 'restore':
        print('已恢复官方服务映射；扩展已保存的代理标识状态不会自动清除。')
    elif mapped and enabled and result['runtime_verified']:
        print('验证方法：在 App 新建任务，让它通过 Edge 扩展读取当前标签页数量。')
    elif not mapped and not enabled:
        print('需要启用时：manage-adapter enable')


def main(argv=None):
    parser = argparse.ArgumentParser(description='CodexPlusPlus-Mac：API-key 模式的 macOS 浏览器适配管理')
    parser.add_argument('action', nargs='?', default='status', choices=['check', 'install', 'alias', 'status', 'enable', 'restore'])
    parser.add_argument('--app', help='明确指定 Codex/ChatGPT .app 路径，仅 check/install 使用')
    parser.add_argument('--descriptor', help='明确指定插件缓存 .mcp.json，仅 install 使用')
    parser.add_argument('--codex-home', help='Codex 用户目录，默认 CODEX_HOME 或 ~/.codex')
    parser.add_argument('--json', action='store_true', help='以 JSON 输出')
    parser.add_argument('--version', action='version', version=VERSION)
    args = parser.parse_args(argv)
    codex_home = Path(args.codex_home or os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()
    root = codex_home / 'codexplusplus-mac'
    require(not root.is_symlink(), '安装目录不能是符号链接')
    if args.action in ['check', 'install']:
        require(sys.platform == 'darwin', '仅支持 macOS')
        app = locate_app(args.app)
        runtime = app / 'Contents/Resources/cua_node'
        if args.action == 'check':
            profile, _, _ = validate_runtime(runtime)
            result = {'action': 'check', 'runtime_verified': True, 'runtime': profile['runtime_archive_version'],
                      'browser_connection': 'not_tested'}
        else:
            descriptor = locate_descriptor(codex_home, args.descriptor)
            install(root, codex_home, app, descriptor)
            result = {'action': 'install', 'command': str(root / 'bin/manage-adapter.py'), 'enabled': False}
    elif args.action == 'status' and not root.exists():
        result = {'action': 'status', 'installed': False, 'browser_connection': 'not_tested'}
    elif args.action == 'alias':
        installed_record(root, codex_home)
        add_alias(root / 'bin/manage-adapter.py', Path.home() / '.zshrc', codex_home)
        result = {'action': 'alias', 'command': str(root / 'bin/manage-adapter.py')}
    else:
        result = manage(root, codex_home, args.action)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print_result(result)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (AdapterError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print('已停止：' + str(error), file=sys.stderr)
        sys.exit(1)
