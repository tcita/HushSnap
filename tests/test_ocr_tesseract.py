from hushsnap import ocr_service


def test_map_language_tag_to_tess_langs():
    assert ocr_service._map_language_tag_to_tess_langs("en-US") == "eng"
    assert ocr_service._map_language_tag_to_tess_langs("zh-CN") == "chi_sim+eng"
    assert ocr_service._map_language_tag_to_tess_langs("zh-TW") == "chi_tra+eng"
    assert ocr_service._map_language_tag_to_tess_langs("ja-JP") == "jpn"


def test_parse_tesseract_tsv_builds_lines():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t96\tHello\n"
        "5\t1\t1\t1\t1\t2\t44\t20\t28\t12\t95\tWorld\n"
    )

    result = ocr_service._parse_tesseract_tsv(tsv)

    assert result.text == "Hello World"
    assert len(result.lines) == 1
    assert result.lines[0].text == "Hello World"
    assert len(result.lines[0].words) == 2
