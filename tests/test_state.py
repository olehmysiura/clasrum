import state as st


def test_new_submission_needs_review():
    assert st.needs_review(st.load_state("/nonexistent/state.json"), "c/w/s", "2026-10-01T10:00:00Z")


def test_processed_submission_is_skipped():
    s = st.mark_processed(st.EMPTY, "c/w/s", "T1", "h1", "now")
    assert not st.needs_review(s, "c/w/s", "T1")
    assert not st.needs_review(s, "c/w/s", "T1", "h1")


def test_resubmission_with_new_content_reviewed_again():
    s = st.mark_processed(st.EMPTY, "c/w/s", "T1", "h1", "now")
    assert st.needs_review(s, "c/w/s", "T2")          # до завантаження — треба перевірити
    assert st.needs_review(s, "c/w/s", "T2", "h2")    # вміст змінився


def test_resubmission_same_content_skipped():
    s = st.mark_processed(st.EMPTY, "c/w/s", "T1", "h1", "now")
    assert not st.needs_review(s, "c/w/s", "T2", "h1")
    s2 = st.touch_turned_in(s, "c/w/s", "T2")
    assert not st.needs_review(s2, "c/w/s", "T2")
    assert s["submissions"]["c/w/s"]["turned_in_at"] == "T1"  # вхідний стан не змінено


def test_mark_processed_is_pure_and_idempotent():
    s1 = st.mark_processed(st.EMPTY, "k", "T", "h", "now", grade="4")
    s2 = st.mark_processed(s1, "k", "T", "h", "now", grade="4")
    assert s1 == s2
    assert st.EMPTY["submissions"] == {}


def test_content_hash_order_independent():
    assert st.content_hash([b"a", "b"]) == st.content_hash(["b", b"a"])
    assert st.content_hash([b"a"]) != st.content_hash([b"b"])


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    s = st.mark_processed(st.EMPTY, "c/w/s", "T1", "h1", "now", grade="5 (відмінно)")
    st.save_state(s, p)
    loaded = st.load_state(p)
    assert loaded["submissions"]["c/w/s"]["grade"] == "5 (відмінно)"
    assert not st.needs_review(loaded, "c/w/s", "T1")
