from trip_hunter.demo import run_demo


def test_demo_runs_and_returns_at_least_one_deal(capsys):
    deals = run_demo()
    assert len(deals) >= 1

    captured = capsys.readouterr()
    assert "COMBINED TRIP DROP" in captured.out
    assert "Trip Score:" in captured.out
