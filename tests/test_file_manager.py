import shutil
import unittest
import uuid
from pathlib import Path

from yolo_viewer.file_manager import scan_dataset


class FileManagerTests(unittest.TestCase):
    def setUp(self):
        self._base = Path.cwd() / "tests" / "_tmp"
        self._base.mkdir(parents=True, exist_ok=True)
        self._case_dir = self._base / f"filemgr_{uuid.uuid4().hex}"
        self._case_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self._case_dir, ignore_errors=True)

    def test_match_images_labels_with_bucket_names(self):
        (self._case_dir / "images_all").mkdir(parents=True, exist_ok=True)
        (self._case_dir / "labels_all").mkdir(parents=True, exist_ok=True)

        (self._case_dir / "images_all" / "sample1.jpg").write_bytes(b"fake")
        (self._case_dir / "labels_all" / "sample1.txt").write_text("0 0.5 0.5 0.1 0.1\n", encoding="utf-8")

        items = scan_dataset(self._case_dir)
        self.assertEqual(len(items), 1)
        self.assertIsNotNone(items[0].image_path)
        self.assertIsNotNone(items[0].label_path)


if __name__ == "__main__":
    unittest.main()
