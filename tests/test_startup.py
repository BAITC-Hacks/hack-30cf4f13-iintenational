"""Storage startup regressions; no access to the user's uploads/history."""
from __future__ import annotations

import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench.server import Storage


class StorageStartupTests(unittest.TestCase):
    def test_directory_modes_preserve_windows_inheritance_and_posix_privacy(self):
        original_mkdir = Path.mkdir
        for platform, expected_mode in (("win32", 0o777), ("linux", 0o700)):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as temporary:
                calls = []

                def record_mkdir(path, mode=0o777, parents=False, exist_ok=False):
                    calls.append((path, mode))
                    return original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

                with patch("sys.platform", platform), patch.object(Path, "mkdir", record_mkdir):
                    store = Storage(Path(temporary) / "store")
                    try:
                        with patch("threading.Thread.start"):
                            job = store.start_run([], {}, {})
                        expected_paths = {
                            store.root, store.upload_dir, store.run_dir,
                            store.run_dir / job["id"], store.run_dir / job["id"] / "artifacts",
                        }
                        self.assertEqual({path for path, _ in calls}, expected_paths)
                        self.assertTrue(all(mode == expected_mode for _, mode in calls), calls)
                    finally:
                        store.close()

    def test_existing_uploads_survive_reopening(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "store"
            first = Storage(root)
            try:
                uploaded = first.upload("example.csv", b"src,dst,amount\nA,B,10\n")
                original_index = (root / "uploads.json").read_bytes()
            finally:
                first.close()
            second = Storage(root)
            try:
                path, record = second.resolve(uploaded["id"])
                self.assertEqual(path.read_bytes(), b"src,dst,amount\nA,B,10\n")
                self.assertEqual(record["filename"], "example.csv")
                self.assertEqual((root / "uploads.json").read_bytes(), original_index)
            finally:
                second.close()

    def test_regular_file_instead_of_upload_directory_is_not_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collision = root / "uploads"
            collision.write_bytes(b"preserve me")
            with self.assertRaisesRegex(ValueError, "не каталог"):
                Storage(root)
            self.assertEqual(collision.read_bytes(), b"preserve me")

    def test_permission_denial_is_explained(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(Path, "mkdir", side_effect=PermissionError(errno.EACCES, "denied")):
                with self.assertRaisesRegex(ValueError, "Нет доступа.*права"):
                    Storage(Path(temporary) / "store")

    def test_windows_file_exists_can_mask_inaccessible_existing_directory(self):
        original_mkdir, original_stat = Path.mkdir, Path.stat
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "store"
            denied = root / "uploads"

            def inaccessible_mkdir(path, *args, **kwargs):
                if path == denied:
                    raise FileExistsError(errno.EEXIST, "WinError 183", str(path))
                return original_mkdir(path, *args, **kwargs)

            def inaccessible_stat(path, *args, **kwargs):
                if path == denied:
                    raise PermissionError(errno.EACCES, "denied", str(path))
                return original_stat(path, *args, **kwargs)

            with patch.object(Path, "mkdir", inaccessible_mkdir), patch.object(Path, "stat", inaccessible_stat):
                with self.assertRaisesRegex(ValueError, "Нет доступа.*права"):
                    Storage(root)


if __name__ == "__main__":
    unittest.main()
