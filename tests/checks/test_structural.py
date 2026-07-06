from corpuslint.config import Config
from corpuslint.models import Chunk
from corpuslint.checks.base import CheckContext
from corpuslint.checks.low_information import LowInformationCheck
from corpuslint.checks.chunk_size import ChunkSizeCheck


def _ctx(texts, config):
    return CheckContext([Chunk(str(i), t, "s") for i, t in enumerate(texts)], None, config)


def test_low_information_flags_thin_and_symbol_chunks():
    cfg = Config(low_info_min_tokens=5)
    findings = LowInformationCheck().run(_ctx(["one two", "----- ==== >>>>", "this is a full sentence here"], cfg))
    flagged = {cid for f in findings for cid in f.chunk_ids}
    assert flagged == {"0", "1"}


def test_chunk_size_flags_out_of_bounds():
    cfg = Config(min_chunk_tokens=2, max_chunk_tokens=4)
    findings = ChunkSizeCheck().run(_ctx(["x", "a b c", "a b c d e f"], cfg))
    flagged = {cid for f in findings for cid in f.chunk_ids}
    assert flagged == {"0", "2"}
