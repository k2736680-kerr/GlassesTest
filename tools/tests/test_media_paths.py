import unittest
from unittest import mock

from glasstest.media_measure import (
    adb_pull_file,
    format_byte_size,
    media_search_tokens,
    parse_device_path,
    parse_media_store_rows,
    pull_from_public_media,
    _parse_ls_size,
    _path_has_non_ascii,
    _public_name_rank,
    _sizes_from_logcat,
)
from glasstest.phone_operations import PhoneOperations, filter_import_paths_by_capture_window
from glasstest.media_paths import (
    MEDIA_RES_REL,
    candidate_media_names,
    classify_path,
    extract_paths_from_import_result,
    media_recency_key,
    pick_latest_typed_paths,
    resolve_media_rel_path,
)


PKG = "com.moyoung.glasses"


class MediaPathTests(unittest.TestCase):
    def test_parse_user0_user10_and_data(self):
        self.assertEqual(
            parse_device_path(f"/data/user/0/{PKG}/files/moyoung/wifi/media_res/a.jpg", PKG),
            "files/moyoung/wifi/media_res/a.jpg",
        )
        self.assertEqual(
            parse_device_path(f"/data/user/10/{PKG}/cache/cache_x_1.jpg", PKG),
            "cache/cache_x_1.jpg",
        )
        self.assertEqual(
            parse_device_path(f"/data/data/{PKG}/files/moyoung/wifi/media_res/a.jpg", PKG),
            "files/moyoung/wifi/media_res/a.jpg",
        )

    def test_candidate_names_photo_and_video(self):
        photos = candidate_media_names("cache_abc_20261028161307976.jpg")
        self.assertIn("image_20261028161307976.jpg", photos)
        self.assertIn("20261028161307976.jpg", photos)
        videos = candidate_media_names("cache_abc_20261028161307976.mp4")
        self.assertIn("stabilized-20261028161307976.mp4", videos)
        self.assertIn("video-20261028161307976.mp4", videos)
        cache_video = candidate_media_names("cache_hash_video-20261104174936816")
        self.assertIn("6stabilized-20261104174936816.mp4", cache_video)
        self.assertIn("stabilized-20261104174936816.mp4", cache_video)
        self.assertEqual(classify_path("cache_hash_video-20261104174936816"), "video")
        self.assertEqual(classify_path("6stabilized-20261104174936816.mp4"), "video")

    def test_resolve_prefers_existing_image_prefix(self):
        existing = {f"{MEDIA_RES_REL}/image_20261028161307976.jpg"}
        reported = f"/data/user/0/{PKG}/cache/cache_hash_20261028161307976.jpg"
        resolved = resolve_media_rel_path(
            reported,
            package=PKG,
            exists_fn=lambda rel: rel in existing,
            list_names_fn=lambda: ["image_20261028161307976.jpg"],
        )
        self.assertEqual(resolved, f"{MEDIA_RES_REL}/image_20261028161307976.jpg")

    def test_resolve_bare_timestamp_on_other_phone(self):
        existing = {f"{MEDIA_RES_REL}/20261028160752977.jpg"}
        reported = f"/data/user/10/{PKG}/cache/cache_hash_20261028160752977.jpg"
        resolved = resolve_media_rel_path(
            reported,
            package=PKG,
            exists_fn=lambda rel: rel in existing,
        )
        self.assertEqual(resolved, f"{MEDIA_RES_REL}/20261028160752977.jpg")

    def test_resolve_by_listing_when_name_unknown(self):
        listed = ["stabilized-20261028161119913.mp4"]
        reported = f"/data/user/0/{PKG}/cache/cache_x_20261028161119913.mp4"
        resolved = resolve_media_rel_path(
            reported,
            package=PKG,
            exists_fn=lambda rel: False,
            list_names_fn=lambda: listed,
        )
        self.assertEqual(resolved, f"{MEDIA_RES_REL}/stabilized-20261028161119913.mp4")

    def test_pick_latest_photo_not_old_video(self):
        paths = [
            f"{MEDIA_RES_REL}/image_20261028160000000.jpg",
            f"{MEDIA_RES_REL}/stabilized-20261028170000000.mp4",
            f"{MEDIA_RES_REL}/image_20261028180000000.jpg",
        ]
        typed = pick_latest_typed_paths(paths, "photo")
        self.assertEqual(max(typed, key=media_recency_key), paths[2])
        self.assertTrue(all(classify_path(p) == "photo" for p in typed))

    def test_extract_import_paths(self):
        result = {
            "success_paths": ["a.jpg", "a.jpg"],
            "current_cycle_success_paths": ["b.mp4"],
        }
        paths = extract_paths_from_import_result(result)
        self.assertEqual(paths, ["b.mp4", "a.jpg"])

    def test_bk_wav_path_with_spaces_counts_as_audio(self):
        line = (
            "event=download_file_item,path=/data/data/com.moyoung.glasses/"
            "files/moyoung/wifi/media_res/2026-10-29 14-59-12.wav"
        )
        event = PhoneOperations._parse_import_log_line(PhoneOperations.__new__(PhoneOperations), line)
        self.assertEqual(
            event["success_paths"],
            ["/data/data/com.moyoung.glasses/files/moyoung/wifi/media_res/2026-10-29 14-59-12.wav"],
        )
        filtered = filter_import_paths_by_capture_window(
            event["success_paths"],
            "2026-10-29 14:58:50",
            "2026-10-29 14:59:38",
        )
        self.assertEqual(filtered["matched_counts"]["audio"], 1)

    def test_public_lookup_does_not_need_brand_folder(self):
        tokens = media_search_tokens(
            "/data/data/com.moyoung.glasses/files/moyoung/wifi/media_res/20261029145530.jpg"
        )
        self.assertIn("20261029145530", tokens)
        self.assertIn("2026-10-29 14-55-30", tokens)
        dashed = media_search_tokens("files/moyoung/wifi/media_res/2026-10-29 14-59-12.wav")
        self.assertIn("2026-10-29 14-59-12", dashed)
        rows = parse_media_store_rows(
            "Row: 0 _data=/storage/emulated/0/Pictures/SomeBrand/20261029145530.jpg\n"
            "Row: 1 _data=/storage/emulated/0/Movies/Other/aw_image_20261029145530.jpg, _display_name=x"
        )
        self.assertEqual(len(rows), 2)
        ranked = sorted(rows, key=lambda item: _public_name_rank(item.split("/")[-1]))
        self.assertTrue(ranked[0].endswith("aw_image_20261029145530.jpg"))
        # Prefer real gallery export over bare timestamp alias.
        names = [
            "/sdcard/Movies/Da Echo/20261104174936816.mp4",
            "/sdcard/Movies/Da Echo/6stabilized-20261104174936816.mp4",
        ]
        preferred = sorted(names, key=lambda item: _public_name_rank(item.split("/")[-1]))[0]
        self.assertTrue(preferred.endswith("6stabilized-20261104174936816.mp4"))

    def test_public_pull_retries_when_media_store_path_disappears(self):
        self.pulls = []
        stale = "/storage/emulated/0/Pictures/Da Echo/aw_image_20261110140944.jpg"
        current = "/storage/emulated/0/Pictures/Da Echo/image_20261110140944.jpg"
        discoveries = [[stale], [stale], [current]]

        class Result:
            def __init__(self, code):
                self.returncode = code
                self.stdout = ""
                self.stderr = "" if code == 0 else "adb: error: failed to stat remote object"

        def fake_run(command, **kwargs):
            self.pulls.append(command[4])
            return Result(0 if command[4] == current else 1)

        with (
            mock.patch("glasstest.media_measure.discover_public_media", side_effect=discoveries),
            mock.patch("glasstest.media_measure._remote_file_exists", return_value=True),
            mock.patch("glasstest.media_measure.subprocess.run", side_effect=fake_run),
            mock.patch("glasstest.media_measure.os.makedirs"),
            mock.patch("glasstest.media_measure.os.path.isfile", return_value=True),
            mock.patch("glasstest.media_measure.os.path.getsize", return_value=128),
            mock.patch("glasstest.media_measure.os.remove"),
            mock.patch("glasstest.media_measure.time.sleep"),
        ):
            pulled = pull_from_public_media(
                serial="SERIAL",
                device_path="files/moyoung/wifi/media_res/20261110140944.jpg",
                local_path="photo.jpg",
            )
        self.assertEqual(pulled, "photo.jpg")
        self.assertEqual(self.pulls, [stale, stale, current])

    def test_format_byte_size_and_non_ascii_path(self):
        self.assertEqual(format_byte_size(1024), "1 KB")
        self.assertEqual(format_byte_size(3057640), "2.9 MB")
        self.assertTrue(_path_has_non_ascii(r"E:\锦仓自动化测试\a.jpg"))
        self.assertFalse(_path_has_non_ascii(r"C:\Temp\a.jpg"))
        self.assertEqual(_parse_ls_size("-rw-rw---- 1 u0_a123 media_rw 3057640 2026-11-16 11:45 x.avi"), 3057640)

    def test_sizes_from_logcat_bk_avi(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "import.log"
            log_path.write_text(
                "BkAviEnhance start, path=20261116114524.avi, bytes=3057640, mode=bk_avi_enhance\n",
                encoding="utf-8",
            )
            sizes = _sizes_from_logcat(str(log_path))
            self.assertEqual(sizes.get("20261116114524.avi"), 3057640)

    def test_adb_pull_stages_non_ascii_local_path(self):
        import tempfile
        from pathlib import Path

        pulls = []

        class Result:
            returncode = 0
            stdout = "1 file pulled"
            stderr = ""

        def fake_run(command, **kwargs):
            pulls.append(list(command))
            Path(command[5]).write_bytes(b"x" * 200)
            return Result()

        with tempfile.TemporaryDirectory() as tmp:
            local_path = str(Path(tmp) / "锦仓" / "横屏_照片.jpg")
            with (
                mock.patch("glasstest.media_measure._remote_file_exists", return_value=True),
                mock.patch("glasstest.media_measure.subprocess.run", side_effect=fake_run),
            ):
                adb_pull_file(
                    serial="SERIAL",
                    remote_path="/sdcard/Pictures/a.jpg",
                    local_path=local_path,
                )
            self.assertTrue(Path(local_path).is_file())
            self.assertEqual(len(pulls), 1)
            self.assertTrue(pulls[0][5].encode("ascii"))

    def test_classify_pull_error_non_ascii(self):
        from glasstest.media_measure import classify_pull_error

        msg = (
            "adb: error: cannot create file/directory "
            "'E:\\锦仓自动化测试\\output\\截图\\横屏_照片.jpg': No such file or directory"
        )
        self.assertEqual(
            classify_pull_error(msg, local_path=r"E:\锦仓自动化测试\output\截图\横屏_照片.jpg"),
            "local_path_non_ascii",
        )
        self.assertEqual(
            classify_pull_error("run-as: package not debuggable: com.moyoung.glasses"),
            "package_not_debuggable",
        )


if __name__ == "__main__":
    unittest.main()
