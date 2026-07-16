"""Integration-test utilities for the PP-OCR layout pipeline.

Render known text at controlled sizes → run detection + clustering →
evaluate against ground truth.

Typical usage (pytest)::

    from scripts.ocr_layout.cases import make_line_clustering_cases
    from scripts.ocr_layout.render import render_case
    from scripts.ocr_layout.pipeline import run_pipeline
    from scripts.ocr_layout.evaluate import check_clustering

    @pytest.mark.parametrize("case", make_line_clustering_cases())
    def test_line_clustering(case, browser, engine):
        render_result = render_case(case, browser)
        pipeline_result = run_pipeline(render_result.png_path, engine)
        verdict = check_clustering(render_result, pipeline_result)
        assert verdict.false_merges == 0, verdict.summary()

Standalone (no pytest)::

    from scripts.ocr_layout.render import render_cases, RenderCase
    from scripts.ocr_layout.pipeline import run_pipeline, get_engine
    from scripts.ocr_layout.evaluate import check_clustering

    engine = get_engine()
    for case in [...]:
        rr = render_cases([case], ...)[0]
        pr = run_pipeline(rr.png_path, engine)
        print(check_clustering(rr, pr).summary())
"""
