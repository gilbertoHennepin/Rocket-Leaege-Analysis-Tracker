#pragma once

#include "CoreMinimal.h"
#include "Core/ReplayCore.h"
#include "Core/TelemetryCore.h"
#include "GameFramework/Actor.h"
#include "RLReplayData.h"
#include "RLTelemetryReceiver.h"

#include "RLReplayDirector.generated.h"

class UStaticMeshComponent;

// A live match started or got its first frame (true: rebuild everything for
// its players), or its coaching moments changed (false).
DECLARE_MULTICAST_DELEGATE_OneParam(FRLOnLiveMatchChanged, bool /*bNewMatch*/);

// What the HUD shows about the live link.
struct FRLLiveStatus
{
	enum class EState : uint8
	{
		Off,      // not listening
		Waiting,  // listening, no match yet
		Live,     // frames arriving
		Quiet,    // nothing for a couple of seconds
		Ended,    // the sender said goodbye
	};

	EState State = EState::Off;
	FString Address;
	double PacketsPerSecond = 0.0;
	double LossPercent = 0.0;
	double JitterMs = 0.0;
	double BufferMs = 0.0;       // how far behind the newest frame playback is held
	double BehindSeconds = 0.0;  // how far the viewer is behind the live edge (paused or scrubbed back)
	bool bFollowing = false;     // showing the live edge
};

// Owns the loaded match and the playback clock, and moves the ball and car
// visuals to match the current replay time every frame. The match comes from
// a file, or arrives live over UDP from `rltracker stream` (docs/telemetry.md).
UCLASS()
class RLREPLAYVIEWER_API ARLReplayDirector : public AActor
{
	GENERATED_BODY()

public:
	ARLReplayDirector();

	bool LoadReplay(const FString& Path, FString& OutError);
	bool HasReplay() const { return !Replay.IsEmpty(); }

	const FRLReplay& GetReplay() const { return Replay; }
	rlcore::PlaybackClock& GetClock() { return Clock; }
	const rlcore::PlaybackClock& GetClock() const { return Clock; }
	const FString& GetLoadedPath() const { return LoadedPath; }

	// Positions at the current time, in world space.
	FVector GetBallLocation() const;
	FVector GetCarLocation(int32 PlayerIndex) const;
	double GetCarBoost(int32 PlayerIndex) const;
	FString GetClockText() const;

	// The player the camera and moment list focus on, or INDEX_NONE.
	int32 GetFocusPlayer() const { return FocusPlayer; }
	void SetFocusPlayer(int32 PlayerIndex);

	// Jump a little before a coaching moment so its lead-up is visible.
	void JumpToMoment(int32 MomentIndex);
	void JumpToNextMoment();
	void JumpToPreviousMoment();
	int32 GetActiveMoment() const { return ActiveMoment; }

	// The most recent moment whose on-screen window contains Time, for one
	// player (or anyone, with INDEX_NONE).
	int32 FindMomentAt(double Time, int32 PlayerIndex) const;

	static constexpr double MomentLeadIn = 2.5;  // seconds shown before a moment

	// Live mode: listen for a stream on Host:Port and play it as it arrives.
	// Loading a file stops it.
	bool StartLive(const FString& Host, int32 Port, FString& OutError);
	void StopLive();
	bool IsLive() const { return Receiver.IsValid() && Receiver->IsRunning(); }

	// Jump to the live edge and stay there. Pausing, seeking or jumping to a
	// moment leaves it; playing back up to the edge rejoins it.
	void GoLive();
	bool IsFollowingLive() const { return IsLive() && bHasLiveInfo && bFollowLive; }
	FRLLiveStatus GetLiveStatus() const;

	FRLOnLiveMatchChanged OnLiveMatchChanged;

	virtual void Tick(float DeltaSeconds) override;

protected:
	virtual void EndPlay(const EEndPlayReason::Type EndPlayReason) override;

private:
	void ResetMatch();
	void TickLive(float DeltaSeconds);
	void PumpLive();
	void ApplyLiveInfo(const rlcore::telemetry::Packet& Packet);
	void AppendLiveFrame(const rlcore::telemetry::Packet& Packet);
	void ApplyLiveEvent(const rlcore::telemetry::Packet& Packet);
	void ClearVisuals();
	void BuildVisuals();
	void UpdateVisuals();
	void DrawBallTrail() const;

	FRLReplay Replay;
	std::vector<double> MomentTimes;
	rlcore::PlaybackClock Clock;
	rlcore::SamplePoint Sample;
	FString LoadedPath;
	int32 FocusPlayer = INDEX_NONE;
	int32 ActiveMoment = INDEX_NONE;

	// Live mode
	TUniquePtr<FRLTelemetryReceiver> Receiver;
	rlcore::telemetry::StreamReceiver Stream;
	std::vector<double> LiveSentTimes;  // each frame's send time, seconds, matching Replay.Times
	bool bHasLiveInfo = false;
	bool bFollowLive = true;
	double LiveEdgeTime = 0.0;   // the match time the playout buffer says to show now
	double LastShownTime = 0.0;  // the clock last tick, to notice a seek
	double RateWindowStart = 0.0;
	int32 PacketsInWindow = 0;
	double PacketsPerSecond = 0.0;
	bool bWarnedCarCount = false;

	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> BallMesh;

	UPROPERTY()
	TArray<TObjectPtr<USceneComponent>> SpawnedComponents;

	// One root per car, moved every frame; the body and nose hang off it.
	TArray<USceneComponent*> Cars;
};
