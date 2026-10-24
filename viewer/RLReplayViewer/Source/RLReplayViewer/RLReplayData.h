// In-memory form of a match exported by `rltracker viewer-export`, and the
// JSON loader for it. See src/rltracker/viewer_export.py for the format.

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
	int32 BlueScore = 0;
	int32 OrangeScore = 0;
	double Fps = 10.0;

	TArray<FRLPlayerInfo> Players;
	std::vector<double> Times;
	std::vector<double> Clock;
	TArray<bool> Live;
	FRLBodyTrack Ball;
	TArray<FRLCarTrack> Cars;  // same order as Players
	TArray<FRLGoal> Goals;
	TArray<FRLMoment> Moments;  // sorted by time

	int32 NumFrames() const { return static_cast<int32>(Times.size()); }
	bool IsEmpty() const { return Times.empty(); }
	double StartTime() const { return Times.empty() ? 0.0 : Times.front(); }
	double EndTime() const { return Times.empty() ? 0.0 : Times.back(); }

	int32 FindPlayer(const FString& Id) const;
	double ClockAt(double Time) const;
	std::vector<double> MomentTimes() const;

	static bool LoadFromFile(const FString& Path, FRLReplay& Out, FString& OutError);
	static bool LoadFromString(const FString& Json, FRLReplay& Out, FString& OutError);
};
