#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "RLLiveCapture.generated.h"

class ARLReplayDirector;
class ARLViewerPawn;

// Scripted check of live mode, for the README screenshot and for testing on a
// machine where nobody is at the keyboard. Launch the viewer with
//   -game -windowed -ResX=1600 -ResY=900 -Live -LiveCapture="/absolute/output/folder"
// and run `rltracker stream`. Once the match arrives it lets it play, then
// saves a still at the live edge, pauses to fall behind and saves another,
// goes live again and saves a third, then quits. Every second it logs the
// link stats, the score and how smoothly playback advanced.
UCLASS()
class RLREPLAYVIEWER_API ARLLiveCapture : public AActor
{
	GENERATED_BODY()

public:
	ARLLiveCapture();

	void Start(ARLReplayDirector* InDirector, ARLViewerPawn* InPawn, const FString& InOutputDir);

	virtual void Tick(float DeltaSeconds) override;

	static constexpr double PlayBeforeFirstShot = 20.0;  // seconds of live play first, past the kickoff
	static constexpr double PausedFor = 4.0;
	static constexpr double RejoinFor = 2.0;
	static constexpr double GiveUpAfter = 120.0;  // no match by then: quit

private:
	enum class EPhase : uint8
	{
		WaitingForMatch,
		Playing,
		Paused,
		Rejoined,
		Finishing,  // the last screenshot is being written
		Done,
	};

	void Capture(const FString& FileName);
	void LogStatus();
	void Finish();

	TWeakObjectPtr<ARLReplayDirector> Director;
	TWeakObjectPtr<ARLViewerPawn> Pawn;
	FString OutputDir;
	EPhase Phase = EPhase::WaitingForMatch;
	double PhaseSeconds = 0.0;
	double TotalSeconds = 0.0;
	double LogSeconds = 0.0;

	// Smoothness while following live: the match time should advance about
	// as fast as real time, every frame, and never go backwards.
	double LastShown = 0.0;
	bool bHasLastShown = false;
	int32 BackwardSteps = 0;
	double MaxStep = 0.0;  // largest match-time step in one frame, seconds
	int32 Ticks = 0;
};
