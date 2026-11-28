"""Outside programs setup installs at a pinned version (state_host_services.md §1.7).

The real functions of scripts/shipped_tools.sh run in bash against a stand-in
download; nothing is fetched from the network.

Run:  python -m unittest harness.tests.test_shipped_tools   (from core-infra)
"""

import configparser
import hashlib
import os
import re
import subprocess
import tempfile
import unittest
import zipfile

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.dirname(CORE_INFRA)
SCRIPT = os.path.join(CORE_INFRA, "scripts", "shipped_tools.sh")
INI = os.path.join(CORE_INFRA, "config", "shipped_tools.ini")


def _fake_rclone(version):
    return f'#!/bin/bash\necho "rclone {version}"\necho "- os/version: test"\n'


class TestInstallRclone(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = self._dir.name
        self.dest = os.path.join(self.tmp, "bin", "rclone")
        os.mkdir(os.path.dirname(self.dest))
        self.zip = os.path.join(self.tmp, "download.zip")
        with zipfile.ZipFile(self.zip, "w") as archive:
            archive.writestr("rclone-v9.9.9-linux-amd64/README.txt", "docs")
            archive.writestr("rclone-v9.9.9-linux-amd64/rclone", _fake_rclone("v9.9.9"))
        with open(self.zip, "rb") as handle:
            self.sum = hashlib.sha256(handle.read()).hexdigest()
        self.asked = os.path.join(self.tmp, "asked")
        # Stand-in for curl: records the URL and copies the local file to -o <path>.
        self.curl = os.path.join(self.tmp, "curl")
        with open(self.curl, "w", encoding="utf-8") as out:
            out.write('#!/bin/bash\nwhile [ $# -gt 0 ]; do case "$1" in -o) dest="$2"; shift ;; http*) url="$1" ;; esac; shift; done\n'
                      f'echo "$url" >> "{self.asked}"\n[ -n "${{FAKE_CURL_FAIL:-}}" ] && exit 22\ncp "{self.zip}" "$dest"\n')
        os.chmod(self.curl, 0o755)

    def _ini(self, checksum=None, version="v9.9.9"):
        path = os.path.join(self.tmp, "tools.ini")
        with open(path, "w", encoding="utf-8") as out:
            out.write("# comment\n[rclone]\n"
                      f"version = {version}\n"
                      "url = https://example.invalid/{version}/rclone-{version}-linux-{arch}.zip\n"
                      f"sha256_amd64 = {checksum or self.sum}\n"
                      f"sha256_arm64 = {checksum or self.sum}\n"
                      "\n[apt]\nfuse3 = 3.14.0\n")
        return path

    def _run(self, ini, machine="x86_64", fail=False):
        env = dict(os.environ, VERSA_RCLONE_DEST=self.dest, VERSA_CURL_BIN=self.curl,
                   VERSA_TOOLS_MACHINE=machine)
        if fail:
            env["FAKE_CURL_FAIL"] = "1"
        done = subprocess.run(
            ["bash", "-c", f'source "{SCRIPT}"; install_rclone "{ini}"; rc=$?; echo "NOTE=${{SHIPPED_TOOL_NOTE}}"; exit $rc'],
            env=env, capture_output=True, text=True,
        )
        return done.returncode, done.stdout.strip().split("NOTE=", 1)[-1]

    def _installed(self):
        if not os.path.isfile(self.dest):
            return ""
        return subprocess.run([self.dest, "version"], capture_output=True, text=True).stdout.splitlines()[0]

    def test_installs_the_pinned_version_when_rclone_is_missing(self):
        rc, note = self._run(self._ini())
        self.assertEqual((rc, note), (0, "rclone v9.9.9"))
        self.assertEqual(self._installed(), "rclone v9.9.9")
        self.assertEqual(oct(os.stat(self.dest).st_mode & 0o777), "0o755")
        with open(self.asked, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(),
                             "https://example.invalid/v9.9.9/rclone-v9.9.9-linux-amd64.zip")

    def test_arm_machines_ask_for_the_arm_build(self):
        self._run(self._ini(), machine="aarch64")
        with open(self.asked, encoding="utf-8") as handle:
            self.assertIn("linux-arm64.zip", handle.read())

    def test_nothing_is_downloaded_when_the_pinned_version_is_there(self):
        self._run(self._ini())
        os.remove(self.asked)
        rc, note = self._run(self._ini())
        self.assertEqual((rc, note), (1, "rclone v9.9.9"))
        self.assertFalse(os.path.exists(self.asked))

    def test_another_version_is_replaced_by_the_pinned_one(self):
        with open(self.dest, "w", encoding="utf-8") as out:
            out.write(_fake_rclone("v1.60.1"))
        os.chmod(self.dest, 0o755)
        rc, _ = self._run(self._ini())
        self.assertEqual(rc, 0)
        self.assertEqual(self._installed(), "rclone v9.9.9")

    def test_a_download_that_does_not_match_its_checksum_is_not_installed(self):
        rc, note = self._run(self._ini(checksum="0" * 64))
        self.assertEqual(rc, 2)
        self.assertIn("does not match its checksum", note)
        self.assertFalse(os.path.exists(self.dest))

    def test_a_wrong_checksum_leaves_the_rclone_that_was_there(self):
        with open(self.dest, "w", encoding="utf-8") as out:
            out.write(_fake_rclone("v1.60.1"))
        os.chmod(self.dest, 0o755)
        rc, _ = self._run(self._ini(checksum="0" * 64))
        self.assertEqual(rc, 2)
        self.assertEqual(self._installed(), "rclone v1.60.1")

    def test_no_network_is_reported_and_setup_carries_on(self):
        rc, note = self._run(self._ini(), fail=True)
        self.assertEqual(rc, 2)
        self.assertIn("could not be downloaded", note)
        self.assertFalse(os.path.exists(self.dest))

    def test_a_machine_type_with_no_pinned_build_is_left_alone(self):
        rc, note = self._run(self._ini(), machine="riscv64")
        self.assertEqual(rc, 2)
        self.assertIn("not pinned for this kind of machine", note)
        self.assertFalse(os.path.exists(self.asked))


def _fake_gh(version):
    return f'#!/bin/bash\necho "gh version {version} (2026-01-01)"\necho "https://github.com/cli/cli/releases/tag/v{version}"\n'


class TestInstallGh(unittest.TestCase):
    """The GitHub CLI, installed the same way as rclone (skill github_cli.md)."""

    def setUp(self):
        import io
        import tarfile
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = self._dir.name
        self.dest = os.path.join(self.tmp, "bin", "gh")
        os.mkdir(os.path.dirname(self.dest))
        self.archive = os.path.join(self.tmp, "download.tar.gz")
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, text, mode in (("gh_9.9.9_linux_amd64/LICENSE", "docs", 0o644),
                                     ("gh_9.9.9_linux_amd64/share/man/man1/gh.1", "man", 0o644),
                                     ("gh_9.9.9_linux_amd64/bin/gh", _fake_gh("9.9.9"), 0o755)):
                data = text.encode()
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), mode
                archive.addfile(info, io.BytesIO(data))
        with open(self.archive, "rb") as handle:
            self.sum = hashlib.sha256(handle.read()).hexdigest()
        self.asked = os.path.join(self.tmp, "asked")
        self.curl = os.path.join(self.tmp, "curl")
        with open(self.curl, "w", encoding="utf-8") as out:
            out.write('#!/bin/bash\nwhile [ $# -gt 0 ]; do case "$1" in -o) dest="$2"; shift ;; http*) url="$1" ;; esac; shift; done\n'
                      f'echo "$url" >> "{self.asked}"\n[ -n "${{FAKE_CURL_FAIL:-}}" ] && exit 22\ncp "{self.archive}" "$dest"\n')
        os.chmod(self.curl, 0o755)

    def _ini(self, checksum=None):
        path = os.path.join(self.tmp, "tools.ini")
        with open(path, "w", encoding="utf-8") as out:
            out.write("[rclone]\nversion = v1.0.0\n\n[gh]\nversion = 9.9.9\n"
                      "url = https://example.invalid/v{version}/gh_{version}_linux_{arch}.tar.gz\n"
                      f"sha256_amd64 = {checksum or self.sum}\nsha256_arm64 = {checksum or self.sum}\n")
        return path

    def _run(self, ini, machine="x86_64", fail=False):
        env = dict(os.environ, VERSA_GH_DEST=self.dest, VERSA_CURL_BIN=self.curl, VERSA_TOOLS_MACHINE=machine)
        if fail:
            env["FAKE_CURL_FAIL"] = "1"
        done = subprocess.run(
            ["bash", "-c", f'source "{SCRIPT}"; install_gh "{ini}"; rc=$?; echo "NOTE=${{SHIPPED_TOOL_NOTE}}"; exit $rc'],
            env=env, capture_output=True, text=True,
        )
        return done.returncode, done.stdout.strip().split("NOTE=", 1)[-1]

    def _installed(self):
        if not os.path.isfile(self.dest):
            return ""
        return subprocess.run([self.dest, "--version"], capture_output=True, text=True).stdout.splitlines()[0]

    def test_installs_the_pinned_version_when_gh_is_missing(self):
        rc, note = self._run(self._ini())
        self.assertEqual((rc, note), (0, "gh 9.9.9"))
        self.assertTrue(self._installed().startswith("gh version 9.9.9"))
        self.assertEqual(oct(os.stat(self.dest).st_mode & 0o777), "0o755")
        with open(self.asked, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), "https://example.invalid/v9.9.9/gh_9.9.9_linux_amd64.tar.gz")

    def test_arm_machines_ask_for_the_arm_build(self):
        self._run(self._ini(), machine="aarch64")
        with open(self.asked, encoding="utf-8") as handle:
            self.assertIn("linux_arm64.tar.gz", handle.read())

    def test_nothing_is_downloaded_when_the_pinned_version_is_there(self):
        self._run(self._ini())
        os.remove(self.asked)
        self.assertEqual(self._run(self._ini()), (1, "gh 9.9.9"))
        self.assertFalse(os.path.exists(self.asked))

    def test_an_older_gh_is_replaced_by_the_pinned_one(self):
        with open(self.dest, "w", encoding="utf-8") as out:
            out.write(_fake_gh("2.45.0"))
        os.chmod(self.dest, 0o755)
        self.assertEqual(self._run(self._ini())[0], 0)
        self.assertTrue(self._installed().startswith("gh version 9.9.9"))

    def test_a_download_that_does_not_match_its_checksum_is_not_installed(self):
        with open(self.dest, "w", encoding="utf-8") as out:
            out.write(_fake_gh("2.45.0"))
        os.chmod(self.dest, 0o755)
        rc, note = self._run(self._ini(checksum="0" * 64))
        self.assertEqual(rc, 2)
        self.assertIn("does not match its checksum", note)
        self.assertTrue(self._installed().startswith("gh version 2.45.0"))   # what was there stays

    def test_no_network_and_an_unpinned_machine_are_reported_and_setup_carries_on(self):
        rc, note = self._run(self._ini(), fail=True)
        self.assertEqual(rc, 2)
        self.assertIn("could not be downloaded", note)
        rc, note = self._run(self._ini(), machine="riscv64")
        self.assertEqual(rc, 2)
        self.assertIn("not pinned for this kind of machine", note)


class TestInstallFuse(unittest.TestCase):
    def _run(self, have_fusermount, apt_works):
        with tempfile.TemporaryDirectory() as tmp:
            fusermount = os.path.join(tmp, "fusermount3")
            apt = os.path.join(tmp, "apt-get")
            if have_fusermount:
                with open(fusermount, "w") as out:
                    out.write("#!/bin/bash\n")
                os.chmod(fusermount, 0o755)
            with open(apt, "w") as out:
                out.write("#!/bin/bash\n" + (f'printf "#!/bin/bash\\n" > "{fusermount}"; chmod 755 "{fusermount}"\n'
                                              if apt_works else "exit 100\n"))
            os.chmod(apt, 0o755)
            env = dict(os.environ, VERSA_FUSERMOUNT_BIN=fusermount, VERSA_APT_BIN=apt)
            return subprocess.run(["bash", "-c", f'source "{SCRIPT}"; install_fuse3'], env=env).returncode

    def test_already_there(self):
        self.assertEqual(self._run(have_fusermount=True, apt_works=False), 1)

    def test_installed_from_the_distribution(self):
        self.assertEqual(self._run(have_fusermount=False, apt_works=True), 0)

    def test_a_failed_install_is_reported_not_fatal(self):
        self.assertEqual(self._run(have_fusermount=False, apt_works=False), 2)


class TestShippedVersions(unittest.TestCase):
    """The pinned-versions file as it ships."""

    def setUp(self):
        self.cfg = configparser.ConfigParser(interpolation=None)
        self.cfg.read(INI, encoding="utf-8")

    def test_rclone_is_pinned_with_a_checksum_for_each_machine_type(self):
        rclone = self.cfg["rclone"]
        self.assertRegex(rclone["version"], r"^v\d+\.\d+\.\d+$")
        self.assertEqual(rclone["url"], "https://downloads.rclone.org/{version}/rclone-{version}-linux-{arch}.zip")
        for arch in ("amd64", "arm64"):
            self.assertRegex(rclone[f"sha256_{arch}"], r"^[0-9a-f]{64}$", arch)
        self.assertNotEqual(rclone["sha256_amd64"], rclone["sha256_arm64"])
        self.assertRegex(rclone["checked"], r"^\d{4}-\d{2}-\d{2}$")

    def test_gh_is_pinned_with_a_checksum_for_each_machine_type(self):
        ini = configparser.ConfigParser()
        ini.read(INI)
        self.assertRegex(ini.get("gh", "version"), r"^\d+\.\d+\.\d+$")       # no "v": the url adds it
        self.assertIn("v{version}/gh_{version}_linux_{arch}.tar.gz", ini.get("gh", "url"))
        self.assertTrue(ini.get("gh", "url").startswith("https://github.com/cli/cli/releases/download/"))
        for arch in ("amd64", "arm64"):
            self.assertRegex(ini.get("gh", f"sha256_{arch}"), r"^[0-9a-f]{64}$")

    def test_the_bash_reader_reads_what_configparser_reads(self):
        for key in ("version", "url", "sha256_amd64", "sha256_arm64"):
            done = subprocess.run(
                ["bash", "-c", f'source "{SCRIPT}"; shipped_tool_value "{INI}" rclone {key}'],
                capture_output=True, text=True,
            )
            self.assertEqual(done.stdout.strip(), self.cfg["rclone"][key], key)

    def test_fuse_and_the_other_system_packages_are_tracked_not_forced(self):
        apt = self.cfg["apt"]
        for package in ("fuse3", "ffmpeg", "xclip", "python3-venv", "python3-pip", "pipx",
                        "git", "sqlite3", "jq", "inotify-tools", "curl", "rsync"):
            self.assertTrue(apt[package].strip(), package)
        self.assertIn("Ubuntu", apt["_reference"])

    def test_every_python_package_setup_asks_for_has_a_shipped_version(self):
        """The harness's requirements file and agictl's package list, name by name."""
        harness = {name.lower() for name in self.cfg["python.harness"] if not name.startswith("_")}
        with open(os.path.join(CORE_INFRA, "harness", "requirements.txt"), encoding="utf-8") as handle:
            wanted = [re.split(r"[<>=!~ \[]", line.strip(), maxsplit=1)[0].lower()
                      for line in handle if line.strip() and not line.strip().startswith("#")]
        self.assertGreaterEqual(len(wanted), 8)
        self.assertEqual([name for name in wanted if name not in harness], [])
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as handle:
            required = re.search(r'^REQUIRED_PKGS="([^"]+)"', handle.read(), flags=re.M).group(1).split()
        agictl = {name.lower() for name in self.cfg["python.agictl"] if not name.startswith("_")}
        self.assertEqual([name for name in required if name.lower() not in agictl], [])

    def test_every_pin_is_one_exact_version(self):
        for section in ("python.harness", "python.agictl"):
            for name, version in self.cfg[section].items():
                if name.startswith("_"):
                    continue
                self.assertRegex(version, r"^[0-9][0-9A-Za-z.+!-]*$", f"{section} {name}")

    def test_the_minimums_in_the_requirements_file_are_met_by_the_pins(self):
        def number(text):
            return tuple(int(part) for part in re.findall(r"\d+", text)[:3])
        pins = self.cfg["python.harness"]
        with open(os.path.join(CORE_INFRA, "harness", "requirements.txt"), encoding="utf-8") as handle:
            for line in handle:
                found = re.match(r"\s*([A-Za-z0-9_.-]+)\s*>=\s*([0-9.]+)", line)
                if found:
                    self.assertGreaterEqual(number(pins[found.group(1).lower()]), number(found.group(2)), line.strip())

    def test_what_floats_says_where_it_comes_from_and_why(self):
        floating = self.cfg["floating"]
        self.assertIn("searxng/searxng:latest", floating["searxng"])
        for name, value in floating.items():
            self.assertGreater(len(value), 40, name)

    def test_the_hugging_face_command_is_pinned_and_setup_installs_that_version(self):
        version = self.cfg["pipx"]["huggingface-hub"]
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertNotIn("huggingface-hub", self.cfg["floating"])
        done = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; shipped_tool_value "{INI}" pipx huggingface-hub'],
                              capture_output=True, text=True)
        self.assertEqual(done.stdout.strip(), version)
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('pipx huggingface-hub || true)"', text)
        # The pinned install first; the unpinned one only when that fails or no version is set.
        self.assertIn('if [ -z "${_HF_VERSION}" ] || ! pipx install "huggingface-hub[cli]==${_HF_VERSION}"; then\n'
                      "          pipx install 'huggingface-hub[cli]'\n", text)

    def test_the_browser_is_pinned_through_playwright(self):
        self.assertEqual(self.cfg["browser"]["_pinned_by"], "python.harness playwright")
        self.assertTrue(self.cfg["python.harness"]["playwright"])


class TestPythonPins(unittest.TestCase):
    """Constraint lines from the file, and the install that uses them."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.tmp = self._dir.name
        self.ini = os.path.join(self.tmp, "tools.ini")
        with open(self.ini, "w", encoding="utf-8") as out:
            out.write("[other]\nrich = 1.0\n\n[python.test]\n# a comment\n_python = 3.12\n_checked = 2026-11-21\n"
                      "click            = 8.3.3\nlangchain-openai = 1.2.1\nPillow=12.2.0\n\n[apt]\nfuse3 = 3.14\n")
        self.calls = os.path.join(self.tmp, "calls")
        # Stand-in for pip: records its arguments and the constraint lines it was given.
        self.pip = os.path.join(self.tmp, "pip")
        with open(self.pip, "w", encoding="utf-8") as out:
            out.write('#!/bin/bash\nargs="$*"; pins=""\n'
                      'while [ $# -gt 0 ]; do [ "$1" = "-c" ] && pins="$2"; shift; done\n'
                      f'echo "ARGS $args" >> "{self.calls}"\n'
                      f'[ -n "$pins" ] && sed "s/^/PIN /" "$pins" >> "{self.calls}"\n'
                      'if [ -n "$pins" ]; then exit "${FAKE_PINNED_RC:-0}"; fi\nexit "${FAKE_PLAIN_RC:-0}"\n')
        os.chmod(self.pip, 0o755)

    def _pins(self, section):
        done = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; shipped_python_pins "{self.ini}" {section}'],
                              capture_output=True, text=True)
        return done.stdout.split()

    def test_one_section_becomes_constraint_lines(self):
        self.assertEqual(self._pins("python.test"), ["click==8.3.3", "langchain-openai==1.2.1", "Pillow==12.2.0"])
        self.assertEqual(self._pins("python.none"), [])

    def _install(self, pinned_rc=0, plain_rc=0, section="python.test"):
        env = dict(os.environ, FAKE_PINNED_RC=str(pinned_rc), FAKE_PLAIN_RC=str(plain_rc))
        done = subprocess.run(
            ["bash", "-c", f'source "{SCRIPT}"; pip_install_pinned "{self.ini}" {section} "{self.pip}" install -q -r reqs.txt'],
            env=env, capture_output=True, text=True,
        )
        with open(self.calls, encoding="utf-8") as handle:
            return done.returncode, handle.read().splitlines()

    def test_installs_with_the_shipped_versions(self):
        rc, calls = self._install()
        self.assertEqual(rc, 0)
        self.assertEqual(len([c for c in calls if c.startswith("ARGS")]), 1)
        self.assertTrue(calls[0].startswith("ARGS install -q -r reqs.txt -c "))
        self.assertEqual(calls[1:], ["PIN click==8.3.3", "PIN langchain-openai==1.2.1", "PIN Pillow==12.2.0"])

    def test_a_machine_where_the_pins_cannot_be_met_still_gets_its_packages(self):
        rc, calls = self._install(pinned_rc=1)
        self.assertEqual(rc, 1)
        runs = [c for c in calls if c.startswith("ARGS")]
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[1], "ARGS install -q -r reqs.txt")

    def test_both_ways_failing_is_reported(self):
        rc, _ = self._install(pinned_rc=1, plain_rc=1)
        self.assertEqual(rc, 2)

    def test_a_section_with_no_pins_installs_without_them(self):
        rc, calls = self._install(section="python.none")
        self.assertEqual(rc, 1)
        self.assertEqual(calls, ["ARGS install -q -r reqs.txt"])

    def test_the_shipped_file_gives_pins_for_both_environments(self):
        for section, least in (("python.harness", 40), ("python.agictl", 5)):
            done = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; shipped_python_pins "{INI}" {section}'],
                                  capture_output=True, text=True)
            lines = done.stdout.split()
            self.assertGreaterEqual(len(lines), least, section)
            self.assertTrue(all(re.match(r"^[A-Za-z0-9_.-]+==[0-9]", line) for line in lines), section)
            self.assertFalse(any(line.startswith("_") for line in lines), section)

    def test_setup_installs_both_environments_through_the_pins(self):
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('pip_install_pinned "${SRC_CORE_INFRA}/config/shipped_tools.ini" python.harness \\\n'
                      '  "${VENV_DIR}/bin/pip" install -q -r "${DEPLOYED_CORE_INFRA}/harness/requirements.txt" || _PIN_RC=$?', text)
        self.assertIn('pip_install_pinned "${SRC_CORE_INFRA}/config/shipped_tools.ini" python.agictl \\\n'
                      '    ${_PIP} install --quiet ${REQUIRED_PKGS} || _PIN_RC=$?', text)
        # The harness install keeps its last resort: the plain command, as before the pins.
        harness = text[text.index("python.harness \\"):text.index("chown -R root:root \"${VENV_DIR}\"")]
        self.assertIn('*) "${VENV_DIR}/bin/pip" install -q -r "${DEPLOYED_CORE_INFRA}/harness/requirements.txt" ;;', harness)


class TestSetupStep(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as handle:
            text = handle.read()
        start = text.index("# ─── Step 11e: Cloud storage tools")
        self.step = text[start:text.index("# ─── System Permissions Restabilization", start)]

    def test_setup_installs_both_from_the_pinned_file(self):
        self.assertIn('source "${SRC_CORE_INFRA}/scripts/shipped_tools.sh"', self.step)
        self.assertIn("install_fuse3 || _FUSE_RC=$?", self.step)
        self.assertIn('install_rclone "${SRC_CORE_INFRA}/config/shipped_tools.ini" || _RCLONE_RC=$?', self.step)
        self.assertIn('install_gh "${SRC_CORE_INFRA}/config/shipped_tools.ini" || _GH_RC=$?', self.step)

    def test_the_step_runs_on_an_update_too(self):
        """Not inside the install-only part of setup (the fault WEB-13 was)."""
        with open(os.path.join(SRC, "setup.sh"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertGreater(text.index("# ─── Step 11e: Cloud storage tools"),
                           text.index("fi  # end UPDATE_MODE=false guard (Step 9)"))
        before = text[:text.index("# ─── Step 11e: Cloud storage tools")]
        # The last install-only block opened before the step is closed before it.
        opened = before.rindex('\nif [ "${UPDATE_MODE}" = false ]; then')
        self.assertIn("\nfi", before[opened:])

    def test_a_failure_is_a_warning_and_never_ends_setup(self):
        self.assertEqual(len(re.findall(r"\*\) warn ", self.step)), 3)
        self.assertNotIn("exit ", self.step)
        self.assertNotIn("fail ", self.step)


if __name__ == "__main__":
    unittest.main()
