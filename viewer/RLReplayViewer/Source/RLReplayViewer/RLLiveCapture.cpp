#include "RLLiveCapture.h"

#include "HAL/PlatformMisc.h"
#include "Misc/Paths.h"
#include "RLReplayDirector.h"
#include "RLReplayViewer.h"
#include "RLViewerPawn.h"
#include "UnrealClient.h"

ARLLiveCapture::ARLLiveCapture()
{
	PrimaryActorTick.bCanEverTick = true;
	// Measure after the director has moved the clock this frame.
	PrimaryActorTick.TickGroup = TG_PostUpdateWork;
}

void ARLLiveCapture::Start(ARLReplayDirector* InDirector, ARLViewerPawn* InPawn, const FString& InOutputDir)
{
	Director = InDirector;
	Pawn = InPawn;
	OutputDir = FPaths::ConvertRelativePathToFull(InOutputDir);
	if (!Director.IsValid() || !Pawn.IsValid() || !Director->IsLive())
	{
		UE_LOG(LogRLViewer, Error, TEXT("Live capture needs live mode (-Live)"));
		FPlatformMisc::RequestExit(false);
		return;
	}
	UE_LOG(LogRLViewer, Display, TEXT("Live capture: waiting for `rltracker stream`, saving into %s"), *OutputDir);
}

void ARLLiveCapture::Capture(const FString& FileName)
{
	FScreenshotRequest::RequestScreenshot(FPaths::Combine(OutputDir, FileName), /*bInShowUI=*/true, false);
	UE_LOG(LogRLViewer, Display, TEXT("Live capture: taking %s"), *FileName);
}

void ARLLiveCapture::LogStatus()
{
	const FRLLiveStatus S = Director->GetLiveStatus();
	static const TCHAR* States[] = {TEXT("off"), TEXT("waiting"), TEXT("live"), TEXT("quiet"), TEXT("ended")};
	const FRLReplay& Replay = Director->GetReplay();
	const double Now = Director->GetClock().GetTime();
	UE_LOG(LogRLViewer, Display,
		TEXT("Live capture: %s, %.0f pkt/s, loss %.1f%%, jitter %.1f ms, buffer %.0f ms, %s, behind %.1f s, ")
			TEXT("shown %.2f of %.2f, score %d-%d, %d goals, %d moments, ")
			TEXT("%d backward steps, max step %.3f s over %d ticks"),
		States[static_cast<int32>(S.State)], S.PacketsPerSecond, S.LossPercent, S.JitterMs, S.BufferMs,
		S.bFollowing ? TEXT("following") : TEXT("not following"), S.BehindSeconds, Now, Replay.EndTime(),
		Replay.ScoreAt(Now, 0), Replay.ScoreAt(Now, 1), Replay.Goals.Num(), Replay.Moments.Num(), BackwardSteps,
		MaxStep, Ticks);
	BackwardSteps = 0;
	MaxStep = 0.0;
	Ticks = 0;
}

void ARLLiveCapture::Finish()
{
	LogStatus();
	UE_LOG(LogRLViewer, Display, TEXT("Live capture finished"));
	Phase = EPhase::Done;
	FPlatformMisc::RequestExit(false);
}

void ARLLiveCapture::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (Phase == EPhase::Done || !Director.IsValid() || !Pawn.IsValid())
	{
		return;
	}
	TotalSeconds += DeltaSeconds;
	PhaseSeconds += DeltaSeconds;

	if (Director->IsFollowingLive())
	{
		const double Shown = Director->GetClock().GetTime();
		if (bHasLastShown)
		{
			const double Step = Shown - LastShown;
			BackwardSteps += Step < -1e-6 ? 1 : 0;
			MaxStep = FMath::Max(MaxStep, Step);
			++Ticks;
		}
		LastShown = Shown;
		bHasLastShown = true;
	}
	else
	{
		bHasLastShown = false;
	}

	LogSeconds += DeltaSeconds;
	if (LogSeconds >= 1.0)
	{
		LogSeconds = 0.0;
		LogStatus();
	}

	// Wait for the last screenshot to be taken before changing what's on screen.
	if (FScreenshotRequest::IsScreenshotRequested())
	{
		return;
	}

	switch (Phase)
	{
	case EPhase::WaitingForMatch:
		if (Director->HasReplay())
		{
			Phase = EPhase::Playing;
			PhaseSeconds = 0.0;
			if (Director->GetFocusPlayer() == INDEX_NONE)
			{
				Director->SetFocusPlayer(0);
			}
			Pawn->SetCameraMode(ERLCameraMode::BallCam);
		}
		else if (TotalSeconds > GiveUpAfter)
		{
			UE_LOG(LogRLViewer, Error, TEXT("Live capture: no match arrived in %.0f s"), GiveUpAfter);
			Finish();
		}
		break;
	case EPhase::Playing:
		if (PhaseSeconds >= PlayBeforeFirstShot)
		{
			Capture(TEXT("viewer-live.png"));
			Phase = EPhase::Pausing;
		}
		break;
	// A screenshot is taken when the frame is drawn, after this tick, so what
	// is on screen only changes once the last one has been written.
	case EPhase::Pausing:
		Director->GetClock().Pause();  // as Space does
		Phase = EPhase::Paused;
		PhaseSeconds = 0.0;
		break;
	case EPhase::Paused:
		if (PhaseSeconds >= PausedFor)
		{
			Capture(TEXT("viewer-live-behind.png"));
			Phase = EPhase::GoingLive;
		}
		break;
	case EPhase::GoingLive:
		Director->GoLive();  // as L does
		Phase = EPhase::Rejoined;
		PhaseSeconds = 0.0;
		break;
	case EPhase::Rejoined:
		if (PhaseSeconds >= RejoinFor)
		{
			Capture(TEXT("viewer-live-rejoined.png"));
			Phase = EPhase::Finishing;
		}
		break;
	case EPhase::Finishing:
		Finish();
		break;
	case EPhase::Done:
		break;
	}
}
