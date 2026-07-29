import tempfile
import unittest
from pathlib import Path

from rime_dict_tool import (
    append_entry,
    find_entries,
    find_weasel_deployer,
    update_entry,
    validate_fields,
)


SAMPLE = """---
name: sample
...
测试\tabcd\t100
同名\taaaa\t10
同名\tbbbb\t20
"""


class DictionaryFunctionsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "dict.yaml"
        self.path.write_text(SAMPLE, encoding="utf-8", newline="")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_find_all_exact_matches(self):
        matches = find_entries(self.path, "同名")
        self.assertEqual([(x.code, x.weight) for x in matches], [("aaaa", "10"), ("bbbb", "20")])

    def test_append_uses_tabs(self):
        append_entry(self.path, "新词", "xyzz", "300")
        self.assertTrue(self.path.read_text(encoding="utf-8").endswith("新词\txyzz\t300\n"))

    def test_update_creates_backup_and_changes_only_selected_line(self):
        selected = find_entries(self.path, "同名")[1]
        backup = update_entry(self.path, selected.line_index, "同名", "同名", "cccc", "999")
        self.assertTrue(backup.exists())
        matches = find_entries(self.path, "同名")
        self.assertEqual([(x.code, x.weight) for x in matches], [("aaaa", "10"), ("cccc", "999")])

    def test_rejects_non_numeric_weight(self):
        with self.assertRaises(ValueError):
            validate_fields("词", "abcd", "高")

    def test_update_preserves_extra_columns(self):
        self.path.write_text("扩展\tabcd\t10\tstem\tmore\n", encoding="utf-8")
        selected = find_entries(self.path, "扩展")[0]
        update_entry(self.path, selected.line_index, "扩展", "扩展", "wxyz", "88")
        self.assertEqual(
            self.path.read_text(encoding="utf-8"),
            "扩展\twxyz\t88\tstem\tmore\n",
        )

    def test_finds_installed_weasel_deployer(self):
        deployer = find_weasel_deployer()
        self.assertIsNotNone(deployer)
        self.assertEqual(deployer.name, "WeaselDeployer.exe")
        self.assertTrue(deployer.is_file())


if __name__ == "__main__":
    unittest.main()
