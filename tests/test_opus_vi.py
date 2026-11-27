from pipeline.acquire.opus_vi import clean_opus_line, group_lines_to_docs


def test_clean_opus_line_strips_html_and_noise():
    assert clean_opus_line("<i>Xin chào   bạn!</i>\x00") == "Xin chào bạn!"
    assert clean_opus_line("...") == ""
    assert clean_opus_line("12345") == ""


def test_group_lines_to_docs_chunks_dialogue():
    lines = [f"Đây là câu hội thoại số {i} bằng tiếng Việt." for i in range(1, 15)]
    docs = list(group_lines_to_docs(lines, max_lines=5, max_chars=5000, min_chars=50))
    assert len(docs) == 3
    assert docs[0][0] == 1 and docs[0][1] == 5
    assert "câu hội thoại số 1" in docs[0][2]
    assert "câu hội thoại số 5" in docs[0][2]
