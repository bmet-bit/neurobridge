from pathlib import Path

from neurobridge.benchmark import benchmark
from neurobridge.fixtures import make_fixture


def test_benchmark_converts_and_measures_fresh_workers(tmp_path: Path) -> None:
    manifest = make_fixture(tmp_path / "fixture", frames=2003, channels=3)
    result = benchmark(manifest, [256, 1024])
    assert result["synthetic"]
    assert result["validated_outputs"] == 2
    for case in result["cases"]:
        (run,) = case["runs"]
        assert run["sample_count"] == 2003
        assert run["chunk_shape"] == [case["buffer_frames"], 3]
        assert run["peak_rss_bytes"] >= run["pre_conversion_peak_rss_bytes"] > 0
        assert case["frames_per_second"] > 0
        assert run["output_bytes"] > 0
