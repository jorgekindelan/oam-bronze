from oam.core.manifest import load_manifest


def test_load_manifest(tmp_path):
    p = tmp_path / "m.yml"
    p.write_text(
        """
country_code: "ZZ"
sources:
  - source_code: "DUMMY"
    source_name: "Dummy"
    enabled: true
    throttle_rps: 1.5
    timeout_s: 12
    headers:
      user-agent: "x"
""".strip(),
        encoding="utf-8",
    )
    mf = load_manifest(p)
    assert mf.country_code == "ZZ"
    assert mf.sources[0].source_code == "DUMMY"
    assert mf.sources[0].throttle_rps == 1.5
    assert mf.sources[0].headers["user-agent"] == "x"
