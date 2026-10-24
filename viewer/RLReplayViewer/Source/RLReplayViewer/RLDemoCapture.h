#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "RLViewerPawn.h"

#include "RLDemoCapture.generated.h"

class ARLReplayDirector;

// Scripted capture of the screenshots and GIF frames used in the README.
// Launch the viewer with
//   -game -windowed -ResX=1600 -ResY=900 -DemoCapture="/absolute/output/folder"
// and it picks the match's most important coaching moment, saves a still from
// each camera and a frame sequence around the moment, then quits. Time runs on
// a fixed step while capturing, so the output is the same on every machine.
UCLASS()
class RLREPLAYVIEWER_API ARLDemoCapture : public AActor
{
	GENERATED_BODY()

public:
	ARLDemoCapture();

	void Start(ARLReplayDirector* InDirector, ARLViewerPawn* InPawn, const FString& InOutputDir);

	virtual void Tick(float DeltaSeconds) override;

	static constexpr double SequenceFps = 15.0;
	static constexpr double SettleSeconds = 1.5;  // let the smoothed camera arrive before a capture

private:
	struct FStep
	{
		FString Name;            // file name for a still, or the prefix for a sequence
		double Time = 0.0;       // replay time to show (the start, for a sequence)
		double Duration = 0.0;   // above 0: capture a sequence this many seconds long
		ERLCameraMode Camera = ERLCameraMode::Orbit;
		int32 Focus = INDEX_NONE;
	};

	void BuildSteps();
	void BeginStep();
	void Capture(const FString& FileName);

	TWeakObjectPtr<ARLReplayDirector> Director;
	TWeakObjectPtr<ARLViewerPawn> Pawn;
	FString OutputDir;
	TArray<FStep> Steps;
	int32 StepIndex = INDEX_NONE;
	double SettleLeft = 0.0;
	int32 Frame = 0;
	bool bAdvance = false;  // the current step is captured; move on next frame
};
