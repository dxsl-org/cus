from pipeline.ingest.wikipedia_dump import wikitext_to_text


def test_wikitext_keeps_prose_drops_markup_and_reference_sections():
    wikitext = """{{Thông tin nhân vật|tên=Hồ Xuân Hương|ghi chú={{lồng}}}}
'''Hồ Xuân Hương''' ({{IPA|hô}}; 1772–1822) là một [[nhà thơ]] [[Việt Nam]] ({{lang|zh|胡春香}}).<ref>Nguồn A</ref>
[[Tập tin:Ho.jpg|nhỏ|Chân dung]]
== Tiểu sử ==
Bà sinh ở [[Nghệ An|xứ Nghệ]].
{| class="wikitable"
| a || b
|}
== Tham khảo ==
* Sách X
[[Thể loại:Nhà thơ Việt Nam]]"""
    assert wikitext_to_text(wikitext) == "Hồ Xuân Hương (1772–1822) là một nhà thơ Việt Nam.\n\nTiểu sử\nBà sinh ở xứ Nghệ."
