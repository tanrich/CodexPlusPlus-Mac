# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tanrich
"""Portable stateful tests; all App/runtime data is generated in a temporary HOME."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]
MANAGER_SOURCE = (PROJECT / "manage-adapter.py").read_bytes()
OFFICIAL_BROWSER = "@oai/browser-desktop/service"


class AdapterFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codexplusplus-tests-")
        self.addCleanup(self.temporary.cleanup)
        # macOS /var is a symlink; CLI canonicalizes CODEX_HOME with resolve().
        self.user = Path(self.temporary.name).resolve() / "Synthetic user 'with spaces'"
        self.user.mkdir()
        self.codex_home = self.user / ".codex"
        self.root = self.codex_home / "codexplusplus-mac"
        self.app = self.user / "Synthetic Codex App.app"
        self.runtime = self.app / "Contents/Resources/cua_node"
        self.assets = self.user / "test assets"
        self.assets.mkdir()
        # Only the project's manager is copied, never a real App or its source.
        self.script = self.assets / "manage-adapter.py"
        self.script.write_bytes(MANAGER_SOURCE)
        spec = importlib.util.spec_from_file_location("synthetic_adapter", self.script)
        self.adapter = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "dont_write_bytecode", True):
            spec.loader.exec_module(self.adapter)
        self.environment = {"HOME": str(self.user), "CODEX_HOME": str(self.codex_home)}
        self.env_patch = mock.patch.dict(os.environ, self.environment)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        source = (
            b"const NO = () => false;\n"
            b"const qe = runtime => ({ session_id: 'test', turn_id: 'test' });\n"
            b"export const service = { runtime: {}, requireIdentification: NO };\n"
            b"export async function handleRpc() { return 'synthetic'; }\n"
        )
        binding = source.index(b"requireIdentification: NO") + len(b"requireIdentification: ")
        manifest = {
            "platform": "darwin", "arch": "arm64", "target": "darwin-arm64",
            "runtime_archive_version": "synthetic-test-runtime-v1",
        }
        files = {
            "manifest.json": self.json_bytes(manifest),
            "bin/node": b"synthetic node; never executed\n",
            "bin/node_repl": b"synthetic node repl\n",
            self.adapter.SERVICE: source,
            "lib/node_modules/@oai/browser-desktop/package.json": b'{"type":"module"}\n',
            "lib/node_modules/@oai/cua-repl/bin/cua-repl.mjs": b"export const synthetic = true;\n",
            "lib/node_modules/@oai/cua-repl/dist/lib/js/oai_js_cua_repl/src/launch.js": b"// synthetic launch\n",
            "lib/node_modules/synthetic-dependency/index.mjs": b"export default 'fixture';\n",
        }
        for relative, data in files.items():
            self.write(self.runtime / relative, data)
        helper = (
            b"function cppNativeIdentificationReader(runtime, fallback, metadata, path) {\n"
            b"  return fallback;\n}\n"
        )
        self.write(self.assets / "vendor/require-identification.mjs", helper)
        self.profile = {
            "schema": 1, "platform": "darwin", "arch": "arm64",
            "runtime_archive_version": manifest["runtime_archive_version"],
            "files": {relative: hashlib.sha256(data).hexdigest() for relative, data in files.items()},
            "binding": {"start": binding, "end": binding + 2, "policy": "NO", "metadata": "qe"},
            "helper_sha256": hashlib.sha256(helper).hexdigest(),
        }
        self.profile_path = self.assets / "assets/runtime-profile.json"
        self.write(self.profile_path, self.json_bytes(self.profile))
        self.write(self.assets / "LICENSE", b"Synthetic test fixture license\n")
        self.write(self.assets / "THIRD_PARTY_NOTICES.md", b"Synthetic test fixture notices\n")
        self.descriptor = self.codex_home / "plugins/cache/test/unified-computer-use/0.0.0/.mcp.json"
        self.document = {
            "user_top_level": {"keep": ["original", 1]},
            "mcpServers": {
                "cua_repl": {
                    "enabled": True, "command": "synthetic-node", "args": ["--user-option"],
                    "user_server_field": {"keep": True},
                    "env": {
                        "NODE_REPL_TRUSTED_SERVICES": json.dumps({
                            "browser": OFFICIAL_BROWSER, "sky": "synthetic-sky-service",
                            "user-service": "synthetic-extra-service",
                        }, indent=2),
                        "NODE_REPL_TRUSTED_CODE_PATHS": os.pathsep.join([
                            str(self.user / "existing trusted root"), str(self.codex_home),
                        ]),
                        "EXTRA_ENV": "user value",
                    },
                },
                "other_server": {"command": "user-owned", "enabled": False},
            },
        }
        self.write(self.descriptor, self.json_bytes(self.document))
        self.descriptor.chmod(0o640)
        self.auth = self.codex_home / "auth.json"
        self.config = self.codex_home / "config.toml"
        self.write(self.auth, b'{"auth_mode":"apikey","OPENAI_API_KEY":"synthetic-only"}\n')
        self.auth.chmod(0o600)
        self.write(self.config, b'model_provider = "synthetic-provider"\n')
        self.control = self.root / "deployment/control.json"
        self.wrapper = str(self.root / "deployment/service-wrapper.mjs")

    @staticmethod
    def json_bytes(value):
        return (json.dumps(value, ensure_ascii=False, indent=4) + "\n\n").encode()

    @staticmethod
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    @staticmethod
    def read_json(path):
        return json.loads(path.read_bytes())

    def save_document(self, document):
        self.descriptor.write_bytes(self.json_bytes(document))

    def set_browser(self, mapping):
        document = self.read_json(self.descriptor)
        env = document["mcpServers"]["cua_repl"]["env"]
        services = json.loads(env["NODE_REPL_TRUSTED_SERVICES"])
        services["browser"] = mapping
        env["NODE_REPL_TRUSTED_SERVICES"] = json.dumps(services)
        self.save_document(document)

    def install(self):
        with mock.patch.object(self.adapter, "syntax_check") as syntax:
            record = self.adapter.install(self.root, self.codex_home, self.app, self.descriptor)
        syntax.assert_called_once()
        self.assertEqual(syntax.call_args.args[0], self.runtime)
        self.assertEqual(syntax.call_args.args[1].name, "browser-service.mjs")
        return record

    def manage(self, action):
        return self.adapter.manage(self.root, self.codex_home, action)

    def main_json(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(self.adapter.main(list(args) + ["--json"]), 0)
        return json.loads(output.getvalue())

    def snapshot(self):
        """Observe bytes, symlink targets, modes and mtimes without following links."""
        result = {}
        for path in [self.user] + sorted(self.user.rglob("*")):
            stat = path.lstat()
            value = os.readlink(path) if path.is_symlink() else (path.read_bytes() if path.is_file() else None)
            result[str(path.relative_to(self.user))] = (value, stat.st_mode, stat.st_mtime_ns)
        return result

    def assert_rejected_without_changes(self, message, operation):
        before = self.snapshot()
        with self.assertRaisesRegex(self.adapter.AdapterError, message):
            operation()
        self.assertEqual(self.snapshot(), before)

    def assert_browser(self, mapping, enabled):
        document = self.read_json(self.descriptor)
        services = json.loads(document["mcpServers"]["cua_repl"]["env"]["NODE_REPL_TRUSTED_SERVICES"])
        self.assertEqual(services["browser"], mapping)
        self.assertIs(self.read_json(self.control)["requireIdentification"], enabled)

    def assert_only_browser_changed(self, expected, mapping):
        actual = self.read_json(self.descriptor)
        expected = copy.deepcopy(expected)
        actual_env = actual["mcpServers"]["cua_repl"]["env"]
        expected_env = expected["mcpServers"]["cua_repl"]["env"]
        actual_services = json.loads(actual_env.pop("NODE_REPL_TRUSTED_SERVICES"))
        expected_services = json.loads(expected_env.pop("NODE_REPL_TRUSTED_SERVICES"))
        expected_services["browser"] = mapping
        self.assertEqual(actual_services, expected_services)
        self.assertEqual(actual, expected)


class InstallTests(AdapterFixture):
    def test_check_validates_generated_hashes_without_writing(self):
        before = self.snapshot()
        with mock.patch.object(self.adapter.sys, "platform", "darwin"):
            result = self.main_json("check", "--app", str(self.app))
        self.assertTrue(result["runtime_verified"])
        self.assertEqual(result["runtime"], "synthetic-test-runtime-v1")
        self.assertEqual(result["browser_connection"], "not_tested")
        self.assertEqual(self.snapshot(), before)

    def test_install_stages_disabled_adapter_and_preserves_descriptor_auth_and_runtime(self):
        before = self.snapshot()
        record = self.install()
        after = self.snapshot()
        for relative, state in before.items():
            # New installation entries change existing directory mtimes only.
            if state[0] is not None:
                self.assertEqual(after[relative], state, relative)
        self.assertEqual(record["codex_home"], str(self.codex_home))
        self.assertEqual(record["descriptor"], str(self.descriptor))
        self.assertEqual(record["profile_sha256"], hashlib.sha256(self.profile_path.read_bytes()).hexdigest())
        self.assert_browser(OFFICIAL_BROWSER, False)
        self.assertEqual((self.root / "descriptor.before-install.json").read_bytes(), self.descriptor.read_bytes())
        self.assertEqual((self.root / "bin/manage-adapter.py").read_bytes(), MANAGER_SOURCE)
        self.assertEqual((self.root / "deployment/control.json").stat().st_mode & 0o777, 0o600)
        dependency = self.root / "deployment/lib/node_modules/synthetic-dependency"
        self.assertTrue(dependency.is_symlink())
        self.assertEqual(dependency.resolve(), self.runtime / "lib/node_modules/synthetic-dependency")
        self.assertFalse((self.root / "deployment" / self.adapter.SERVICE).is_symlink())
        self.assertTrue(self.manage("status")["runtime_verified"])

    def test_install_cli_uses_explicit_paths(self):
        with mock.patch.object(self.adapter.sys, "platform", "darwin"), mock.patch.object(self.adapter, "syntax_check"):
            result = self.main_json("install", "--app", str(self.app), "--descriptor", str(self.descriptor))
        self.assertFalse(result["enabled"])
        self.assertEqual(result["command"], str(self.root / "bin/manage-adapter.py"))
        self.assert_browser(OFFICIAL_BROWSER, False)

    def test_install_rejects_existing_installation_without_overwriting(self):
        self.install()
        self.assert_rejected_without_changes("已存在安装目录", self.install)

    def test_install_rejects_unknown_browser_mapping(self):
        self.set_browser("user-owned-browser-adapter")
        self.assert_rejected_without_changes("已有其他浏览器适配", self.install)
        self.assertFalse(self.root.exists())

    def test_tampered_runtime_is_rejected_before_installation(self):
        for relative in self.profile["files"]:
            with self.subTest(relative=relative):
                path = self.runtime / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"tampered\n")
                    self.assert_rejected_without_changes("文件校验未通过", self.install)
                    self.assertFalse(self.root.exists())
                finally:
                    path.write_bytes(original)

    def test_tampered_helper_is_rejected(self):
        self.write(self.assets / "vendor/require-identification.mjs", b"tampered helper")
        self.assert_rejected_without_changes("上游辅助函数已变化", self.install)

    def test_invalid_binding_is_rejected_even_with_matching_hashes(self):
        self.profile["binding"]["start"] += 1
        self.write(self.profile_path, self.json_bytes(self.profile))
        self.assert_rejected_without_changes("回调位置不匹配", self.install)

    def test_unsupported_platform_and_manifest_version_are_rejected(self):
        cases = [("arch", "x86_64"), ("runtime_archive_version", "synthetic-v2")]
        original = (self.runtime / "manifest.json").read_bytes()
        for key, value in cases:
            with self.subTest(key=key):
                manifest = json.loads(original)
                manifest[key] = value
                data = self.json_bytes(manifest)
                self.write(self.runtime / "manifest.json", data)
                self.profile["files"]["manifest.json"] = hashlib.sha256(data).hexdigest()
                self.write(self.profile_path, self.json_bytes(self.profile))
                self.assert_rejected_without_changes("平台或版本不匹配", self.install)

    def test_runtime_profile_cannot_escape_runtime_root(self):
        outside = self.runtime.parent / "outside"
        self.write(outside, b"outside fixture")
        self.profile["files"]["../outside"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        self.write(self.profile_path, self.json_bytes(self.profile))
        self.assert_rejected_without_changes("runtime 文件越界", self.install)

    def test_runtime_profile_rejects_symlinked_file(self):
        path = self.runtime / "bin/node"
        target = self.runtime / "bin/synthetic-target"
        self.write(target, path.read_bytes())
        path.unlink()
        path.symlink_to(target)
        self.assert_rejected_without_changes("文件缺失或为链接", self.install)

    def test_syntax_failure_removes_stage_without_changing_user_files(self):
        before = {path: path.read_bytes() for path in [self.auth, self.config, self.descriptor]}
        with mock.patch.object(self.adapter, "syntax_check", side_effect=self.adapter.AdapterError("synthetic syntax failure")):
            with self.assertRaisesRegex(self.adapter.AdapterError, "synthetic syntax failure"):
                self.adapter.install(self.root, self.codex_home, self.app, self.descriptor)
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.codex_home.glob(".codexplusplus-mac-*")), [])
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)

    def test_install_detects_descriptor_change_during_generation(self):
        changed = copy.deepcopy(self.document)
        changed["user_top_level"]["added_during_install"] = True
        with mock.patch.object(self.adapter, "syntax_check", side_effect=lambda *args: self.save_document(changed)):
            with self.assertRaisesRegex(self.adapter.AdapterError, "安装期间插件配置发生变化"):
                self.adapter.install(self.root, self.codex_home, self.app, self.descriptor)
        self.assertEqual(self.read_json(self.descriptor), changed)
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.codex_home.glob(".codexplusplus-mac-*")), [])

    def test_install_rechecks_runtime_after_candidate_generation(self):
        path = self.runtime / "bin/node"
        with mock.patch.object(self.adapter, "syntax_check", side_effect=lambda *args: path.write_bytes(b"updated runtime")):
            with self.assertRaisesRegex(self.adapter.AdapterError, "文件校验未通过"):
                self.adapter.install(self.root, self.codex_home, self.app, self.descriptor)
        self.assertFalse(self.root.exists())
        self.assertEqual(list(self.codex_home.glob(".codexplusplus-mac-*")), [])

    def test_descriptor_discovery_refuses_outside_cache_symlinks_and_ambiguity(self):
        outside = self.user / "outside.mcp.json"
        self.write(outside, self.descriptor.read_bytes())
        self.assert_rejected_without_changes("描述符必须位于", lambda: self.adapter.locate_descriptor(self.codex_home, str(outside)))
        link = self.descriptor.parent / "linked.json"
        link.symlink_to(self.descriptor)
        self.assert_rejected_without_changes("不能是符号链接", lambda: self.adapter.locate_descriptor(self.codex_home, str(link)))
        second = self.codex_home / "plugins/cache/test/unified-computer-use/0.0.1/.mcp.json"
        self.write(second, self.descriptor.read_bytes())
        self.assert_rejected_without_changes("无法唯一定位", lambda: self.adapter.locate_descriptor(self.codex_home))


class LifecycleTests(AdapterFixture):
    def setUp(self):
        super().setUp()
        self.install()

    def test_enable_and_restore_preserve_sky_extra_env_and_all_user_edits(self):
        auth_before, config_before = self.auth.read_bytes(), self.config.read_bytes()
        changed = self.read_json(self.descriptor)
        changed["user_top_level"]["added_after_install"] = ["keep", 2]
        changed["mcpServers"]["other_server"]["user_edit"] = True
        server = changed["mcpServers"]["cua_repl"]
        server["args"].append("--new-user-argument")
        server["env"]["ADDED_ENV"] = "keep me"
        services = json.loads(server["env"]["NODE_REPL_TRUSTED_SERVICES"])
        services["sky"] = "updated-user-sky"
        services["new-service"] = "new-user-service"
        server["env"]["NODE_REPL_TRUSTED_SERVICES"] = json.dumps(services)
        self.save_document(changed)
        self.write(self.control, self.json_bytes({"schema": 1, "requireIdentification": False, "user_control_field": "keep"}))
        self.assertTrue(self.manage("enable")["runtime_verified"])
        self.assert_browser(self.wrapper, True)
        self.assert_only_browser_changed(changed, self.wrapper)
        # A further edit made while enabled also survives restore.
        enabled_document = self.read_json(self.descriptor)
        enabled_document["mcpServers"]["cua_repl"]["env"]["EDIT_WHILE_ENABLED"] = "keep too"
        self.save_document(enabled_document)
        self.manage("restore")
        self.assert_browser(OFFICIAL_BROWSER, False)
        self.assert_only_browser_changed(enabled_document, OFFICIAL_BROWSER)
        self.assertEqual(self.read_json(self.control)["user_control_field"], "keep")
        self.assertEqual(self.auth.read_bytes(), auth_before)
        self.assertEqual(self.config.read_bytes(), config_before)
        self.assertEqual(self.descriptor.stat().st_mode & 0o777, 0o640)

    def test_restore_recovers_exact_original_descriptor_bytes_when_no_user_edits(self):
        original = self.descriptor.read_bytes()
        self.manage("enable")
        self.manage("restore")
        self.assertEqual(self.descriptor.read_bytes(), original)
        self.assert_browser(OFFICIAL_BROWSER, False)

    def test_restore_reuses_noncompact_mapping_and_keeps_other_edits_while_enabled(self):
        original_mapping = self.document["mcpServers"]["cua_repl"]["env"]["NODE_REPL_TRUSTED_SERVICES"]
        self.assertIn("\n", original_mapping)
        self.manage("enable")
        edited = self.read_json(self.descriptor)
        edited["user_top_level"]["edited_while_enabled"] = "keep"
        edited["mcpServers"]["cua_repl"]["env"]["NEW_ENV"] = "keep too"
        self.save_document(edited)
        self.manage("restore")
        restored = self.read_json(self.descriptor)
        restored_mapping = restored["mcpServers"]["cua_repl"]["env"]["NODE_REPL_TRUSTED_SERVICES"]
        self.assertEqual(restored_mapping, original_mapping)
        self.assert_only_browser_changed(edited, OFFICIAL_BROWSER)
        self.assert_browser(OFFICIAL_BROWSER, False)

    def test_repeated_enable_and_restore_are_idempotent_without_file_writes(self):
        for action in ["enable", "restore"]:
            with self.subTest(action=action):
                first = self.manage(action)
                before = self.snapshot()
                with mock.patch.object(self.adapter, "replace_file", side_effect=AssertionError("unexpected write")):
                    second = self.manage(action)
                self.assertEqual(second, first)
                self.assertEqual(self.snapshot(), before)

    def test_unknown_mapping_is_rejected_for_every_management_action(self):
        self.set_browser("user-owned-adapter")
        for action in ["enable", "restore", "status"]:
            with self.subTest(action=action):
                self.assert_rejected_without_changes("browser 已指向其他适配器", lambda: self.manage(action))

    def test_enable_refuses_tampered_deployment_assets_and_backup(self):
        paths = [
            self.root / "deployment" / self.adapter.SERVICE,
            self.root / "deployment/service-wrapper.mjs",
            self.root / "bin/assets/runtime-profile.json",
            self.root / "bin/vendor/require-identification.mjs",
            self.root / "descriptor.before-install.json",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"tampered\n")
                    self.assert_rejected_without_changes("已变化|不符合已验证补丁", lambda: self.manage("enable"))
                    self.assert_browser(OFFICIAL_BROWSER, False)
                finally:
                    path.write_bytes(original)

    def test_enable_refuses_tampered_runtime(self):
        self.write(self.runtime / "bin/node_repl", b"updated runtime")
        self.assert_rejected_without_changes("文件校验未通过", lambda: self.manage("enable"))

    def test_enable_will_not_expand_missing_or_sibling_trust_roots(self):
        for trusted in [None, "", str(self.codex_home) + "-sibling", str(self.user / "other trusted root")]:
            with self.subTest(trusted=trusted):
                document = self.read_json(self.descriptor)
                env = document["mcpServers"]["cua_repl"]["env"]
                if trusted is None:
                    env.pop("NODE_REPL_TRUSTED_CODE_PATHS", None)
                else:
                    env["NODE_REPL_TRUSTED_CODE_PATHS"] = trusted
                self.save_document(document)
                self.assert_rejected_without_changes("不会扩大信任范围", lambda: self.manage("enable"))

    def test_disabled_browser_blocks_enable(self):
        document = self.read_json(self.descriptor)
        document["mcpServers"]["cua_repl"]["enabled"] = False
        self.save_document(document)
        self.assert_rejected_without_changes("浏览器工具配置为关闭", lambda: self.manage("enable"))

    def test_malformed_control_is_rejected_without_writes(self):
        for control in [{"schema": 2, "requireIdentification": False}, {"schema": 1, "requireIdentification": 1}]:
            with self.subTest(control=control):
                self.write(self.control, self.json_bytes(control))
                self.assert_rejected_without_changes("control 格式无效", lambda: self.manage("enable"))

    def test_status_is_read_only_before_and_after_enable(self):
        for enabled in [False, True]:
            with self.subTest(enabled=enabled):
                if enabled:
                    self.manage("enable")
                before = self.snapshot()
                with mock.patch.object(self.adapter, "replace_file", side_effect=AssertionError("status wrote a file")):
                    result = self.main_json("status")
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(result["browser_mapping"], "adapter" if enabled else "official")
                self.assertIs(result["requireIdentification"], enabled)
                self.assertTrue(result["runtime_verified"])
                self.assertEqual(result["browser_connection"], "not_tested")
                self.assertFalse(result["auth_files_changed"])
                self.assertFalse(result["app_files_changed"])

    def test_status_reports_invalid_runtime_without_writing(self):
        self.write(self.runtime / "bin/node", b"runtime changed")
        before = self.snapshot()
        result = self.manage("status")
        self.assertFalse(result["runtime_verified"])
        self.assertIn("bin/node", result["verification_error"])
        self.assertEqual(self.snapshot(), before)

    def test_enable_second_file_write_failure_rolls_back_descriptor(self):
        descriptor_before = self.descriptor.read_bytes()
        control_before = self.control.read_bytes()
        auth_before = self.auth.read_bytes()
        real_replace = self.adapter.os.replace
        destinations = []

        def fail_control_replace(source, destination):
            destinations.append(Path(destination))
            if Path(destination) == self.control:
                raise OSError("synthetic control replacement failure")
            return real_replace(source, destination)

        with mock.patch.object(self.adapter.os, "replace", side_effect=fail_control_replace):
            with self.assertRaisesRegex(OSError, "synthetic control replacement failure"):
                self.manage("enable")
        self.assertEqual(destinations, [self.descriptor, self.control, self.descriptor])
        self.assertEqual(self.descriptor.read_bytes(), descriptor_before)
        self.assertEqual(self.control.read_bytes(), control_before)
        self.assertEqual(self.auth.read_bytes(), auth_before)
        self.assertEqual(list(self.descriptor.parent.glob("..mcp.json-*")), [])
        self.assertEqual(list(self.control.parent.glob(".control.json-*")), [])
        self.assert_browser(OFFICIAL_BROWSER, False)

    def test_enable_first_file_failure_does_not_touch_control(self):
        before = self.snapshot()
        with mock.patch.object(self.adapter.os, "replace", side_effect=OSError("synthetic first write failure")):
            with self.assertRaisesRegex(OSError, "synthetic first write failure"):
                self.manage("enable")
        after = self.snapshot()
        for relative, state in before.items():
            if state[0] is not None:
                self.assertEqual(after[relative], state, relative)
        self.assert_browser(OFFICIAL_BROWSER, False)

    def test_concurrent_control_edit_is_preserved_and_descriptor_rolled_back(self):
        descriptor_before = self.descriptor.read_bytes()
        real_replace = self.adapter.replace_file
        changed_control = self.json_bytes({"schema": 1, "requireIdentification": False, "concurrent": "user edit"})

        def change_control_before_write(path, value, previous):
            if Path(path) == self.control:
                self.control.write_bytes(changed_control)
            return real_replace(path, value, previous)

        with mock.patch.object(self.adapter, "replace_file", side_effect=change_control_before_write):
            with self.assertRaisesRegex(self.adapter.AdapterError, "文件被其他程序更改"):
                self.manage("enable")
        self.assertEqual(self.descriptor.read_bytes(), descriptor_before)
        self.assertEqual(self.control.read_bytes(), changed_control)

    def test_restore_still_works_after_runtime_update_or_removal(self):
        for change in ["update", "remove"]:
            with self.subTest(change=change):
                original_node = (self.runtime / "bin/node").read_bytes()
                self.manage("enable")
                if change == "update":
                    self.write(self.runtime / "bin/node", b"synthetic runtime v2")
                else:
                    (self.runtime / "bin/node").unlink()
                result = self.manage("restore")
                self.assertFalse(result["runtime_verified"])
                self.assertIsNotNone(result["verification_error"])
                self.assert_browser(OFFICIAL_BROWSER, False)
                self.write(self.runtime / "bin/node", original_node)

    def test_restore_preserves_user_changes_after_trust_is_removed(self):
        self.manage("enable")
        document = self.read_json(self.descriptor)
        server = document["mcpServers"]["cua_repl"]
        server["enabled"] = False
        server["env"].pop("NODE_REPL_TRUSTED_CODE_PATHS")
        self.save_document(document)
        self.assertFalse(self.manage("restore")["runtime_verified"])
        self.assert_browser(OFFICIAL_BROWSER, False)
        self.assert_only_browser_changed(document, OFFICIAL_BROWSER)

    def test_installed_cli_status_enable_restore_work_without_app_discovery(self):
        for action in ["status", "enable", "restore"]:
            with self.subTest(action=action):
                result = subprocess.run(
                    [sys.executable, str(self.root / "bin/manage-adapter.py"), action, "--json"],
                    env=dict(os.environ, **self.environment), cwd=self.user,
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["action"], action)
        self.assert_browser(OFFICIAL_BROWSER, False)


class StatusAndAliasTests(AdapterFixture):
    def test_uninstalled_status_and_default_action_are_read_only(self):
        before = self.snapshot()
        for args in [(), ("status",)]:
            with self.subTest(args=args):
                result = self.main_json(*args)
                self.assertFalse(result["installed"])
                self.assertEqual(result["browser_connection"], "not_tested")
                self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.root.exists())

    def test_cli_refuses_symlinked_installation_root(self):
        self.root.symlink_to(self.assets, target_is_directory=True)
        self.assert_rejected_without_changes("安装目录不能是符号链接", lambda: self.main_json("status"))

    def test_alias_is_idempotent_preserves_zshrc_and_has_original_backup(self):
        self.install()
        zshrc = self.user / ".zshrc"
        original = b"# existing user setup\nexport USER_SETTING=keep\n"
        self.write(zshrc, original)
        zshrc.chmod(0o640)
        self.main_json("alias")
        after_first = self.snapshot()
        self.main_json("alias")
        self.assertEqual(self.snapshot(), after_first)
        self.assertTrue(zshrc.read_bytes().startswith(original))
        self.assertEqual((self.root / "zshrc.before-alias").read_bytes(), original)
        self.assertEqual(zshrc.stat().st_mode & 0o777, 0o640)
        self.assertEqual(zshrc.read_text().count("alias manage-adapter="), 1)

    def test_alias_creates_new_zshrc_under_temporary_home_only(self):
        self.install()
        zshrc = self.user / ".zshrc"
        self.assertFalse(zshrc.exists())
        self.main_json("alias")
        self.assertTrue(zshrc.is_file())
        self.assertEqual((self.root / "zshrc.before-alias").read_bytes(), b"")
        self.assertEqual(zshrc.stat().st_mode & 0o777, 0o600)

    def test_alias_rejects_existing_conflict_without_backup_or_changes(self):
        self.install()
        self.write(self.user / ".zshrc", b"alias manage-adapter='python3 /user/other-manager.py'\n")
        self.assert_rejected_without_changes("已存在其他 manage-adapter alias", lambda: self.main_json("alias"))
        self.assertFalse((self.root / "zshrc.before-alias").exists())

    def test_alias_refuses_symlinked_zshrc(self):
        self.install()
        target = self.user / "user-shell-config"
        self.write(target, b"# user config\n")
        (self.user / ".zshrc").symlink_to(target)
        self.assert_rejected_without_changes("文件缺失或为链接", lambda: self.main_json("alias"))

    def test_alias_runs_installed_cli_with_spaces_and_apostrophes_in_path(self):
        self.install()
        self.main_json("alias")
        alias_line = next(line for line in (self.user / ".zshrc").read_text().splitlines() if line.startswith("alias "))
        assignment = shlex.split(alias_line)[1]
        command = assignment.split("=", 1)[1]
        self.assertEqual(shlex.split(command), [
            "python3", str(self.root / "bin/manage-adapter.py"), "--codex-home", str(self.codex_home),
        ])
        # The embedded custom home must win even in a different shell environment.
        shell_environment = dict(os.environ, **self.environment)
        shell_environment["CODEX_HOME"] = str(self.user / "other codex home")
        result = subprocess.run(
            ["/bin/sh", "-c", alias_line + "\nmanage-adapter status --json\n"],
            env=shell_environment, cwd=self.user,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["browser_mapping"], "official")

    def test_add_alias_keeps_backward_compatible_two_argument_usage(self):
        self.install()
        script = self.root / "bin/manage-adapter.py"
        zshrc = self.user / ".zshrc"
        self.assertTrue(self.adapter.add_alias(script, zshrc))
        alias_line = next(line for line in zshrc.read_text().splitlines() if line.startswith("alias "))
        command = shlex.split(alias_line)[1].split("=", 1)[1]
        self.assertEqual(shlex.split(command), ["python3", str(script)])
        before = self.snapshot()
        self.assertFalse(self.adapter.add_alias(script, zshrc))
        self.assertEqual(self.snapshot(), before)

    def test_alias_cli_embeds_explicit_home_instead_of_environment_default(self):
        self.install()
        other_home = self.user / "environment default codex home"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(other_home)}):
            self.main_json("alias", "--codex-home", str(self.codex_home))
        alias_line = next(line for line in (self.user / ".zshrc").read_text().splitlines() if line.startswith("alias "))
        command = shlex.split(alias_line)[1].split("=", 1)[1]
        self.assertEqual(shlex.split(command)[-2:], ["--codex-home", str(self.codex_home)])
        self.assertFalse(other_home.exists())


if __name__ == "__main__":
    unittest.main()
