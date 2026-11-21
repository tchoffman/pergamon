from rag import weather

PERIODS = [{"name": "Today", "detailedForecast": "Mostly cloudy, with a high near 75."},
           {"name": "Tonight", "detailedForecast": "Slight chance of showers. Low around 57."}]


def test_live_forecast_for_a_named_place():
    text = weather.forecast("Austin, TX", periods=PERIODS, geocode=lambda p: (30.27, -97.74))
    assert text.startswith("Live forecast for Austin, TX") and "high near 75" in text


def test_offline_says_so():
    def no_net(*a):
        raise OSError("no route to host")
    text = weather.forecast("", geocode=no_net, fetch=no_net)
    assert "No live forecast" in text or "No internet" in text
    assert "No internet" in weather.forecast("Austin", geocode=no_net)


def test_unknown_place():
    assert "Couldn't find" in weather.forecast("Atlantis", geocode=lambda p: None)


def test_assistant_has_the_tool():
    from rag import agent
    schemas, fns = agent.tools_for({"max_distance": 350}, agent.Run(), is_host=False)
    assert "weather" in fns and any(s["function"]["name"] == "weather" for s in schemas)
