import shutil
import unittest
import uuid
from pathlib import Path

from yolo_viewer.validator import parse_yolo_label


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self._base = Path.cwd() / "tests" / "_tmp"
        self._base.mkdir(parents=True, exist_ok=True)
        self._case_dir = self._base / f"validator_{uuid.uuid4().hex}"
        self._case_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._case_dir, ignore_errors=True)

    def test_parse_5_columns(self):
        p = self._case_dir / "a.txt"
        p.write_text("0 0.5 0.5 0.2 0.3\n", encoding="utf-8")
        anns, issues = parse_yolo_label(p)
        self.assertEqual(len(anns), 1)
        self.assertEqual(len(issues), 0)
        self.assertIsNone(anns[0].confidence)

    def test_parse_6_columns_with_confidence(self):
        p = self._case_dir / "b.txt"
        p.write_text("1 0.4 0.6 0.2 0.1 0.88\n", encoding="utf-8")
        anns, issues = parse_yolo_label(p)
        self.assertEqual(len(anns), 1)
        self.assertEqual(len(issues), 0)
        self.assertAlmostEqual(anns[0].confidence or 0.0, 0.88, places=3)


if __name__ == "__main__":
    unittest.main()
