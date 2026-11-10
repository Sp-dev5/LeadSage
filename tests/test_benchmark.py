"""The benchmark arithmetic, on small hand-made results. No model."""

from agent import benchmark
from agent.benchmark import Pair


PAIRS = [Pair("qualify", "qualify"), Pair("qualify", "review"), Pair("review", "qualify"),
         Pair("disqualify", "review"), Pair("disqualify", "disqualify"), Pair("review", "disqualify")]


def test_worth_a_reps_time_counts_review_and_qualify_as_yes():
    r = benchmark.rates(PAIRS, {"qualify", "review"})
    assert (r.tp, r.fp, r.fn, r.tn) == (3, 1, 1, 1)
    assert (r.precision, r.recall) == (0.75, 0.75)


def test_qualify_alone():
    r = benchmark.rates(PAIRS, {"qualify"})
    assert (r.tp, r.fp, r.fn, r.tn) == (1, 1, 1, 3)


def test_confusion_rows_are_labels_and_columns_are_verdicts():
    table = benchmark.confusion(PAIRS)
    assert table["disqualify"] == {"qualify": 0, "review": 1, "disqualify": 1}
    assert sum(sum(row.values()) for row in table.values()) == len(PAIRS)


def test_no_positives_gives_zero_not_a_crash():
    r = benchmark.rates([Pair("disqualify", "disqualify")], {"qualify"})
    assert (r.precision, r.recall) == (0.0, 0.0)


def test_rows_without_a_label_or_a_verdict_are_skipped(tmp_path):
    path = tmp_path / "results.csv"
    path.write_text("name,my_label,ollama_verdict\nA,qualify,review\nB,,qualify\nC,review,\nD,maybe,qualify\n",
                    encoding="utf-8")
    assert benchmark.read_pairs(path) == [Pair("qualify", "review")]


def test_the_saved_human_results_all_read():
    for name in benchmark.HUMAN_SETS:
        assert benchmark.read_pairs(benchmark.DATA / name)
