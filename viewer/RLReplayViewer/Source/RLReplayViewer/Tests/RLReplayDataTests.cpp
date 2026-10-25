// Unreal automation tests for the viewer file loader. Run them in the editor
// from Tools > Session Frontend > Automation, filter "RLReplayViewer".
// The engine-free playback math has its own tests in viewer/core-tests.

#include "Misc/AutomationTest.h"
#include "RLReplayData.h"

#if WITH_DEV_AUTOMATION_TESTS

// A macro so it works whether EAutomationTestFlags is the older namespaced enum or the 5.5+ enum class.
#define RL_TEST_FLAGS (EAutomationTestFlags::EditorContext | EAutomationTestFlags::ClientContext | EAutomationTestFlags::ProductFilter)

namespace
{
// Two frames, one player, one goal and two moments listed out of order.
const TCHAR* ValidJson = TEXT(R"json({
	"format": "rltracker-viewer", "version": 1,
	"source": "test.replay", "mode": "1v1", "playlist": "Ranked Duel",
	"score": {"blue": 1, "orange": 0}, "fps": 10, "recordedBy": "p1",
	"players": [{"id": "p1", "name": "Alpha", "team": 0, "car": "Octane"}],
	"frames": {
		"time": [0.0, 0.1], "clock": [300, 299.9], "live": [1, 0],
		"ball": {"pos": [0,0,93, 10,0,93], "rot": [0,0,0,1, 0,0,0,1]},
		"cars": [{"pos": [0,-4600,17, 0,-4590,17], "rot": [0,0,0,1, 0,0,0,1], "boost": [33, 34]}]
	},
	"goals": [{"time": 0.1, "team": 0, "scorerId": "p1", "clock": "4:59"}],
	"touches": [],
	"moments": [
		{"time": 0.1, "clock": "4:59", "playerId": "p1", "kind": "b", "rule": "r", "title": "Second",
		 "severity": "high", "detail": "d", "punished": true},
		{"time": 0.0, "clock": "5:00", "playerId": "p1", "kind": "a", "rule": "r", "title": "First",
		 "severity": "low", "detail": "d", "punished": false}
	]
})json");
}  // namespace

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRLReplayLoadsValidFile, "RLReplayViewer.Data.LoadsValidFile", RL_TEST_FLAGS)

bool FRLReplayLoadsValidFile::RunTest(const FString& Parameters)
{
	FRLReplay Replay;
	FString Error;
	if (!TestTrue(TEXT("loads"), FRLReplay::LoadFromString(ValidJson, Replay, Error)))
	{
		AddError(Error);
		return false;
	}

	TestEqual(TEXT("mode"), Replay.Mode, FString(TEXT("1v1")));
	TestEqual(TEXT("frames"), Replay.NumFrames(), 2);
	TestEqual(TEXT("blue score"), Replay.BlueScore, 1);
	TestEqual(TEXT("players"), Replay.Players.Num(), 1);
	TestEqual(TEXT("recorder found"), Replay.FindPlayer(Replay.RecordedBy), 0);
	TestEqual(TEXT("unknown player"), Replay.FindPlayer(TEXT("nobody")), INDEX_NONE);
	TestFalse(TEXT("second frame not live"), Replay.Live[1]);
	TestEqual(TEXT("boost"), Replay.Cars[0].Boost[1], 34.0);
	TestEqual(TEXT("goals"), Replay.Goals.Num(), 1);
	TestEqual(TEXT("0-0 before the goal"), Replay.ScoreAt(0.05, 0), 0);
	TestEqual(TEXT("1-0 after it"), Replay.ScoreAt(0.1, 0), 1);
	TestEqual(TEXT("clock at start"), Replay.ClockAt(0.0), 300.0);

	TestEqual(TEXT("moments"), Replay.Moments.Num(), 2);
	TestEqual(TEXT("moments sorted by time"), Replay.Moments[0].Title, FString(TEXT("First")));
	TestTrue(TEXT("punished flag"), Replay.Moments[1].bPunished);
	return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRLReplayRejectsBadFiles, "RLReplayViewer.Data.RejectsBadFiles", RL_TEST_FLAGS)

bool FRLReplayRejectsBadFiles::RunTest(const FString& Parameters)
{
	const FString Valid(ValidJson);
	auto Rejects = [this](const TCHAR* What, const FString& Json) {
		FRLReplay Replay;
		FString Error;
		TestFalse(What, FRLReplay::LoadFromString(Json, Replay, Error));
		TestFalse(FString::Printf(TEXT("%s has an error message"), What), Error.IsEmpty());
	};

	Rejects(TEXT("not json"), TEXT("{nope"));
	Rejects(TEXT("wrong format"), Valid.Replace(TEXT("rltracker-viewer"), TEXT("something-else")));
	Rejects(TEXT("future version"), Valid.Replace(TEXT("\"version\": 1"), TEXT("\"version\": 2")));
	Rejects(TEXT("short ball track"), Valid.Replace(TEXT("[0,0,93, 10,0,93]"), TEXT("[0,0,93]")));
	Rejects(TEXT("short boost track"), Valid.Replace(TEXT("[33, 34]"), TEXT("[33]")));
	Rejects(TEXT("unsorted times"), Valid.Replace(TEXT("[0.0, 0.1]"), TEXT("[0.1, 0.0]")));
	return true;
}

// Live mode: match info joined mid-match at 2-1, then a goal and a moment
// arriving as events (src/rltracker/telemetry.py writes these).
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FRLReplayLoadsLiveStream, "RLReplayViewer.Data.LoadsLiveStream", RL_TEST_FLAGS)

bool FRLReplayLoadsLiveStream::RunTest(const FString& Parameters)
{
	FRLReplay Replay;
	FString Error;
	const TCHAR* Hello = TEXT(R"json({"source": "test.replay", "mode": "2v2", "playlist": "Ranked Doubles",
		"fps": 30, "recordedBy": "p1", "score": {"blue": 2, "orange": 1},
		"players": [{"id": "p1", "name": "\u00c5lpha", "team": 0, "car": "Octane"},
		            {"id": "p2", "name": "Bravo", "team": 1, "car": "Fennec"}]})json");
	if (!TestTrue(TEXT("loads match info"), FRLReplay::LoadLiveInfo(Hello, Replay, Error)))
	{
		AddError(Error);
		return false;
	}
	TestEqual(TEXT("players"), Replay.Players.Num(), 2);
	TestEqual(TEXT("an empty track per player"), Replay.Cars.Num(), 2);
	TestTrue(TEXT("no frames yet"), Replay.IsEmpty());
	TestEqual(TEXT("non-ASCII name"), Replay.Players[0].Name, FString(TEXT("\u00C5lpha")));
	TestEqual(TEXT("joined at 2-1"), Replay.ScoreAt(0.0, 0), 2);
	TestEqual(TEXT("joined at 2-1 (orange)"), Replay.ScoreAt(0.0, 1), 1);

	FRLLiveEvent Goal;
	TestTrue(TEXT("goal event"), FRLLiveEvent::Load(TEXT(R"json({"event": "goal", "time": 50.0, "team": 1,
		"scorerId": "p2", "clock": "3:10", "score": {"blue": 2, "orange": 2}})json"), Goal, Error));
	TestTrue(TEXT("is a goal"), Goal.bGoal);
	Replay.AddGoal(Goal.Goal);
	TestEqual(TEXT("2-1 before"), Replay.ScoreAt(49.0, 1), 1);
	TestEqual(TEXT("2-2 after"), Replay.ScoreAt(50.0, 1), 2);

	FRLLiveEvent Late, Early;
	TestTrue(TEXT("moment event"), FRLLiveEvent::Load(TEXT(R"json({"event": "moment", "time": 40.0, "clock": "3:20",
		"playerId": "p1", "kind": "double_commit", "rule": "r", "title": "Late", "severity": "high",
		"detail": "d", "punished": true})json"), Late, Error));
	FRLLiveEvent::Load(TEXT(R"json({"event": "moment", "time": 30.0, "title": "Early"})json"), Early, Error);
	TestFalse(TEXT("is a moment"), Late.bGoal);
	TestEqual(TEXT("moment kind kept"), Late.Moment.Kind, FString(TEXT("double_commit")));
	Replay.AddMoment(Late.Moment);
	Replay.AddMoment(Early.Moment);  // arrives out of order
	TestEqual(TEXT("moments kept in time order"), Replay.Moments[0].Title, FString(TEXT("Early")));

	FRLLiveEvent Unknown;
	TestFalse(TEXT("unknown event rejected"), FRLLiveEvent::Load(TEXT(R"json({"event": "demo"})json"), Unknown, Error));
	TestFalse(TEXT("no players rejected"), FRLReplay::LoadLiveInfo(TEXT(R"json({"players": []})json"), Replay, Error));
	return true;
}

#undef RL_TEST_FLAGS

#endif  // WITH_DEV_AUTOMATION_TESTS
