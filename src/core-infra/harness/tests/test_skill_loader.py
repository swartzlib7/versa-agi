"""A skill an agent is told to load arrives whole (SKR-03)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

CORE_INFRA = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, CORE_INFRA)

from harness import skill_loader as sl  # noqa: E402

SHIPPED = os.path.join(CORE_INFRA, "skills")
# The limit an agent's shell read is cut at by default (init_agents_db.sh).
DEFAULT_TOOL_LIMIT = 5000


class LoaderCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent_dir = os.path.join(self.tmp.name, ".agent")
        self.skills = os.path.join(self.agent_dir, "skills")
        os.makedirs(os.path.join(self.skills, "solution_architect"))
        os.makedirs(os.path.join(self.agent_dir, "docs"))
        self.long = "# Long skill\n" + ("line of procedure\n" * 800) + "LAST LINE OF THE SKILL\n"
        self.write(os.path.join(self.skills, "long_skill.md"), self.long)
        self.write(os.path.join(self.skills, "task_scheduling.md"), "# Task scheduling\nbody\n")
        self.write(os.path.join(self.skills, "solution_architect", "template.sh"), "#!/bin/bash\necho ok\n")
        self.write(os.path.join(self.agent_dir, "docs", "versa_agi_readme.md"), "# Readme\n" + "x" * 9000)
        self.write(os.path.join(self.agent_dir, "system.md"), "PROMPT")
        self.write(os.path.join(self.tmp.name, "outside.md"), "OUTSIDE")

    @staticmethod
    def write(path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)


class TestWhole(LoaderCase):
    def test_a_skill_longer_than_the_tool_limit_arrives_complete(self):
        self.assertGreater(len(self.long), DEFAULT_TOOL_LIMIT * 2)
        text = sl.read_skill("long_skill", self.skills)
        self.assertEqual(text, self.long)
        self.assertTrue(text.rstrip().endswith("LAST LINE OF THE SKILL"))
        self.assertNotIn("TRUNCATED", text)

    def test_every_shipped_skill_arrives_complete(self):
        names = [n for n in os.listdir(SHIPPED) if n.endswith(".md")]
        self.assertGreater(len(names), 30)
        longer = 0
        for name in names:
            with open(os.path.join(SHIPPED, name), encoding="utf-8") as handle:
                want = handle.read()
            self.assertEqual(sl.read_skill(name, SHIPPED), want, name)
            longer += len(want) > DEFAULT_TOOL_LIMIT
        # The reason for the tool: most shipped skills do not fit a shell read.
        self.assertGreater(longer, 15)

    def test_the_name_may_be_written_several_ways(self):
        for name in ("task_scheduling", "task_scheduling.md", " `task_scheduling.md` ",
                     "skills/task_scheduling.md", ".agent/skills/task_scheduling.md"):
            self.assertTrue(sl.read_skill(name, self.skills).startswith("# Task scheduling"), name)

    def test_a_file_in_a_skills_own_folder_and_a_shipped_document(self):
        self.assertIn("echo ok", sl.read_skill("solution_architect/template.sh", self.skills))
        doc = sl.read_skill("docs/versa_agi_readme.md", self.skills)
        self.assertEqual(len(doc), len("# Readme\n") + 9000)
        self.assertEqual(sl.read_skill(".agent/docs/versa_agi_readme.md", self.skills), doc)


class TestOnlyTheAgentsOwnFolders(LoaderCase):
    def test_nothing_outside_the_two_folders_is_read(self):
        for name in ("../system.md", "../../outside.md", "/etc/passwd", "~/x.md",
                     "solution_architect/../../system.md", "docs/../system.md", "docs/../../outside.md"):
            text = sl.read_skill(name, self.skills)
            self.assertTrue(text.startswith("ERROR:"), name)
            self.assertNotIn("PROMPT", text)
            self.assertNotIn("OUTSIDE", text)

    def test_a_link_that_leaves_the_folder_is_not_followed(self):
        os.symlink(os.path.join(self.tmp.name, "outside.md"), os.path.join(self.skills, "linked.md"))
        text = sl.read_skill("linked", self.skills)
        self.assertTrue(text.startswith("ERROR:"))
        self.assertNotIn("OUTSIDE", text)

    def test_an_unknown_name_gets_a_suggestion(self):
        text = sl.read_skill("task_schedule", self.skills)
        self.assertTrue(text.startswith("ERROR: No skill named"))
        text = sl.read_skill("scheduling", self.skills)
        self.assertIn("task_scheduling.md", text)

    def test_no_name_no_folder_and_an_empty_file(self):
        self.assertTrue(sl.read_skill("", self.skills).startswith("ERROR:"))
        self.assertTrue(sl.read_skill("x", "").startswith("ERROR:"))
        self.write(os.path.join(self.skills, "empty.md"), "  \n")
        self.assertTrue(sl.read_skill("empty", self.skills).startswith("ERROR:"))

    def test_the_folder_comes_from_the_harness_or_the_agents_environment(self):
        self.assertEqual(sl.skills_folder("/a/skills", {}), "/a/skills")
        self.assertEqual(sl.skills_folder(None, {"AGICTL_AGENT_DIR": "/home/x/.agent"}), "/home/x/.agent/skills")
        self.assertEqual(sl.skills_folder(None, {}), "")


class TestWiring(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(CORE_INFRA, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_the_tool_is_registered_and_does_not_go_through_the_cut(self):
        text = self.read("harness/agent_harness.py")
        self.assertIn('@tool("agictl_load_skill", args_schema=LoadSkillInput)', text)
        self.assertIn("ALL_TOOLS.append(agictl_load_skill)", text)
        body = text[text.index("def agictl_load_skill("):text.index("ALL_TOOLS.append(agictl_load_skill)")]
        self.assertNotIn("_run_agictl", body)
        self.assertIn("_SKILLS_DIR = skills_dir", text)

    def test_no_shipped_text_tells_an_agent_to_cat_a_skill(self):
        import glob
        files = glob.glob(os.path.join(CORE_INFRA, "skills", "*.md")) + [
            os.path.join(CORE_INFRA, "config", "coa_poise.md"),
            os.path.join(CORE_INFRA, "config", "task_protocol.md"),
            os.path.join(CORE_INFRA, "config", "roles", "agent_poise.md"),
            os.path.join(CORE_INFRA, "harness", "agent_harness.py"),
            os.path.join(CORE_INFRA, "harness", "triage.py"),
        ]
        for path in files:
            with open(path, encoding="utf-8") as handle:
                self.assertNotIn('bash "cat .agent/skills', handle.read().replace('\\"', '"'), path)

    def test_the_catalog_block_and_the_triage_line_name_the_tool(self):
        text = self.read("harness/triage.py")
        self.assertIn("**`agictl_load_skill`**", text)
        self.assertIn("load each with agictl_load_skill", text)


if __name__ == "__main__":
    unittest.main()
