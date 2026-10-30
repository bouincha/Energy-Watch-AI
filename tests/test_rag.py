from app.rag.retriever import chunk_markdown, get_retriever


def test_chunking_keeps_sections_and_overlap():
    words = " ".join(f"w{i}" for i in range(300))
    text = f"# Title\n\n## Section A\n{words}\n\n## Section B\nshort text"
    chunks = chunk_markdown("doc", text, max_words=140, overlap=30)
    a = [c for c in chunks if c.section == "Section A"]
    assert len(a) == 3
    assert a[0].text.split()[-30:] == a[1].text.split()[:30]
    assert chunks[-1].citation == "Title > Section B"


def test_retrieval_finds_the_right_document():
    r = get_retriever()
    assert r.search("compressed air leak at night")[0]["doc"] == "compressed_air"
    assert r.search("PUE drift")[0]["doc"] == "data_center_cooling"
    assert r.search("lights on during the weekend")[0]["doc"] == "lighting"


def test_irrelevant_query_returns_nothing():
    assert get_retriever().search("zzz qqq xyzzy") == []
