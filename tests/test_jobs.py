from PIL import Image

from jason import jobs


def make_photo(path, size):
    Image.new("RGB", size, "white").save(path)
    return str(path)


def test_photo_gate_counts_and_flags(tmp_path, store):
    job = jobs.get_or_create_job(store, "t1", "a@b.com")
    good = [make_photo(tmp_path / f"g{i}.jpg", (2000, 1400)) for i in range(12)]
    weak = [make_photo(tmp_path / "small.jpg", (400, 300))]
    job["photos"] = good + weak
    result = jobs.check_photos(job)
    assert result["total"] == 13
    assert result["usable"] == 12
    assert result["enough"] is True
    assert result["weak_photos"][0]["file"] == "small.jpg"


def test_not_enough_photos(tmp_path, store):
    job = jobs.get_or_create_job(store, "t2", "a@b.com")
    job["photos"] = [make_photo(tmp_path / f"g{i}.jpg", (2000, 1400)) for i in range(5)]
    assert jobs.check_photos(job)["enough"] is False


def test_intake_complete_requires_fields_and_photos(tmp_path, store):
    job = jobs.get_or_create_job(store, "t3", "a@b.com")
    ok, why = jobs.intake_complete(job)
    assert not ok and "missing" in why

    job["intake"] = {f: "x" for f in jobs.INTAKE_FIELDS}
    ok, why = jobs.intake_complete(job)
    assert not ok and "usable photos" in why

    job["photos"] = [make_photo(tmp_path / f"g{i}.jpg", (2000, 1400)) for i in range(12)]
    ok, why = jobs.intake_complete(job)
    assert ok


def test_orientation_agnostic_resolution(tmp_path, store):
    job = jobs.get_or_create_job(store, "t4", "a@b.com")
    # A portrait photo (short side wide enough) should pass.
    job["photos"] = [make_photo(tmp_path / "portrait.jpg", (1400, 2000))]
    assert jobs.check_photos(job)["usable"] == 1
