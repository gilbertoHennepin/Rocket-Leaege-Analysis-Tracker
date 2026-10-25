// In-memory form of a match exported by `rltracker viewer-export`, and the
// JSON loader for it. See src/rltracker/viewer_export.py for the format. A
// live stream (docs/telemetry.md) fills the same structure as it arrives.

#pragma once

#include "CoreMinimal.h"
#include "Core/ReplayCore.h"

#include <vector>

struct FRLPlayerInfo
{
	FString Id;
	FString Name;
	int32 Team = 0;  // 0 blue, 1 orange
	FString Car;
};

// Per-frame position (flat xyz) and rotation (flat xyzw quaternion).
struct FRLBodyTrack
{
	std::vector<double> Pos;
	std::vector<double> Rot;
};

struct FRLCarTrack : FRLBodyTrack
{
	std::vector<double> Boost;  // 0-100 per frame
};

struct FRLGoal
{
	double Time = 0.0;
	int32 Team = 0;
	FString ScorerId;
	FString Clock;
	int32 ScoreAfter[2] = {0, 0};  // blue and orange score once this goal counts
};

// A coaching moment, for example a double commit, with the tip it belongs to.
struct FRLMoment
{
	double Time = 0.0;
	FString Clock;
	FString PlayerId;
	FString Kind;
	FString Rule;
	FString Title;
	FString Severity;  // "low", "medium" or "high"
	FString Detail;
	bool bPunished = false;
};

struct RLREPLAYVIEWER_API FRLReplay
{
	static constexpr int32 SupportedVersion = 1;

	FString Source;
	FString Mode;
	FString Playlist;
	FString RecordedBy;
	int32 BlueScore = 0;  // the file's final score; for a live match, the latest
	int32 OrangeScore = 0;
	int32 StartScore[2] = {0, 0};  // score at the first frame: 0-0, or when a live viewer joined
	double Fps = 10.0;

	TArray<FRLPlayerInfo> Players;
	std::vector<double> Times;
	std::vector<double> Clock;
	TArray<bool> Live;
	FRLBodyTrack Ball;
	TArray<FRLCarTrack> Cars;  // same order as Players
	TArray<FRLGoal> Goals;  // sorted by time
	TArray<FRLMoment> Moments;  // sorted by time

	int32 NumFrames() const { return static_cast<int32>(Times.size()); }
	bool IsEmpty() const { return Times.empty(); }
	double StartTime() const { return Times.empty() ? 0.0 : Times.front(); }
	double EndTime() const { return Times.empty() ? 0.0 : Times.back(); }

	int32 FindPlayer(const FString& Id) const;
	double ClockAt(double Time) const;
	std::vector<double> MomentTimes() const;

	// Score at a replay time: from the last goal by then, or the starting score.
	int32 ScoreAt(double Time, int32 Team) const;

	// Insert keeping time order (live events can arrive slightly out of order).
	void AddGoal(const FRLGoal& Goal);
	void AddMoment(const FRLMoment& Moment);

	static bool LoadFromFile(const FString& Path, FRLReplay& Out, FString& OutError);
	static bool LoadFromString(const FString& Json, FRLReplay& Out, FString& OutError);

	// A live stream's match info (hello) packet: the match details, the players
	// with an empty track each, and the score when it was sent (as the starting
	// score, for a viewer that joins mid-match).
	static bool LoadLiveInfo(const FString& Json, FRLReplay& Out, FString& OutError);
};

// A live stream's event packet: a goal or a coaching moment.
struct RLREPLAYVIEWER_API FRLLiveEvent
{
	bool bGoal = false;
	FRLGoal Goal;
	FRLMoment Moment;

	static bool Load(const FString& Json, FRLLiveEvent& Out, FString& OutError);
};
