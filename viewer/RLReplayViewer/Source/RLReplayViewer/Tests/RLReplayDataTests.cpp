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

#undef RL_TEST_FLAGS

#endif  // WITH_DEV_AUTOMATION_TESTS
