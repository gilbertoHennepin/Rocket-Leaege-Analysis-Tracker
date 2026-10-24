#include "RLDemoCapture.h"

#include "HAL/PlatformMisc.h"
#include "Misc/App.h"
#include "Misc/Paths.h"
#include "RLReplayDirector.h"
#include "RLReplayViewer.h"
#include "UnrealClient.h"

namespace
{
int32 SeverityRank(const FString& Severity)
{
	if (Severity == TEXT("high"))
	{
		return 3;
	}
	return Severity == TEXT("medium") ? 2 : 1;
}
}  // namespace

ARLDemoCapture::ARLDemoCapture()
{
	PrimaryActorTick.bCanEverTick = true;
}

void ARLDemoCapture::Start(ARLReplayDirector* InDirector, ARLViewerPawn* InPawn, const FString& InOutputDir)
{
	Director = InDirector;
	Pawn = InPawn;
	OutputDir = FPaths::ConvertRelativePathToFull(InOutputDir);
	if (!Director.IsValid() || !Pawn.IsValid() || !Director->HasReplay())
	{
		UE_LOG(LogRLViewer, Error, TEXT("Demo capture needs a loaded replay"));
		FPlatformMisc::RequestExit(false);
		return;
	}

	// Seek the clock before the director moves the cars, in the same frame.
	Director->AddTickPrerequisiteActor(this);

	// Fixed step: every frame advances the replay by exactly one sequence frame,
	// however long the screenshot takes to write.
	FApp::SetUseFixedTimeStep(true);
	FApp::SetFixedDeltaTime(1.0 / SequenceFps);

	BuildSteps();
	StepIndex = 0;
	BeginStep();
	UE_LOG(LogRLViewer, Display, TEXT("Demo capture: %d steps into %s"), Steps.Num(), *OutputDir);
}

void ARLDemoCapture::BuildSteps()
{
	const FRLReplay& Replay = Director->GetReplay();
	const int32 Recorder = Replay.FindPlayer(Replay.RecordedBy);

	// The moment to feature: most severe first, then punished, then the
	// recorder's own. It needs a few seconds of lead-in for the sequence.
	int32 Best = INDEX_NONE;
	int32 BestScore = -1;
	for (int32 I = 0; I < Replay.Moments.Num(); ++I)
	{
		const FRLMoment& M = Replay.Moments[I];
		if (M.Time < Replay.StartTime() + 5.0 || M.Time > Replay.EndTime() - 3.0)
		{
			continue;
		}
		const int32 Score = SeverityRank(M.Severity) * 4 + (M.bPunished ? 2 : 0)
			+ (Replay.FindPlayer(M.PlayerId) == Recorder ? 1 : 0);
		if (Score > BestScore)
		{
			Best = I;
			BestScore = Score;
		}
	}

	double Time = Replay.StartTime() + 0.4 * (Replay.EndTime() - Replay.StartTime());
	int32 Focus = Recorder;
	if (Best != INDEX_NONE)
	{
		Time = Replay.Moments[Best].Time;
		Focus = Replay.FindPlayer(Replay.Moments[Best].PlayerId);
		UE_LOG(LogRLViewer, Display, TEXT("Featuring moment %d: %s at %s"), Best, *Replay.Moments[Best].Title,
			*Replay.Moments[Best].Clock);
	}

	// Stills are taken just after the moment, while its label is on screen.
	Steps.Add(FStep{TEXT("viewer-orbit"), Time - 1.0, 0.0, ERLCameraMode::Orbit, INDEX_NONE});
	Steps.Add(FStep{TEXT("viewer-ballcam"), Time + 0.4, 0.0, ERLCameraMode::BallCam, Focus});
	Steps.Add(FStep{TEXT("viewer-topdown"), Time + 0.4, 0.0, ERLCameraMode::TopDown, Focus});
	Steps.Add(FStep{TEXT("viewer-demo"), Time - ARLReplayDirector::MomentLeadIn, 4.5, ERLCameraMode::BallCam, Focus});
}

void ARLDemoCapture::BeginStep()
{
	const FStep& Step = Steps[StepIndex];
	rlcore::PlaybackClock& Clock = Director->GetClock();
	Clock.Pause();
	Clock.Seek(Step.Time);
	Director->SetFocusPlayer(Step.Focus);
	Pawn->SetCameraMode(Step.Camera);
	SettleLeft = SettleSeconds;
	Frame = 0;
}

void ARLDemoCapture::Capture(const FString& FileName)
{
	const FString Path = FPaths::Combine(OutputDir, FileName);
	FScreenshotRequest::RequestScreenshot(Path, /*bInShowUI=*/true, /*bAddFilenameSuffix=*/false);
}

void ARLDemoCapture::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (StepIndex == INDEX_NONE || !Director.IsValid() || !Pawn.IsValid())
	{
		return;
	}
	// One capture per rendered frame: wait until the last one has been taken
	// before changing what's on screen.
	if (FScreenshotRequest::IsScreenshotRequested())
	{
		return;
	}
	if (bAdvance)
	{
		bAdvance = false;
		if (++StepIndex < Steps.Num())
		{
			BeginStep();
		}
		return;
	}
	if (StepIndex >= Steps.Num())
	{
		UE_LOG(LogRLViewer, Display, TEXT("Demo capture finished"));
		StepIndex = INDEX_NONE;
		FPlatformMisc::RequestExit(false);
		return;
	}
	if (SettleLeft > 0.0)
	{
		SettleLeft -= DeltaSeconds;
		return;
	}

	const FStep& Step = Steps[StepIndex];
	if (Step.Duration <= 0.0)
	{
		Capture(Step.Name + TEXT(".png"));
		bAdvance = true;
		return;
	}

	const double Time = Step.Time + Frame / SequenceFps;
	if (Time > Step.Time + Step.Duration + 1e-6)
	{
		bAdvance = true;
		return;
	}
	Director->GetClock().Seek(Time);
	Capture(FString::Printf(TEXT("%s_%03d.png"), *Step.Name, Frame));
	++Frame;
}
