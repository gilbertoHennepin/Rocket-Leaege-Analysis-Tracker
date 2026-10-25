#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class ARLReplayDirector;
class ARLViewerGameMode;
class ARLViewerPawn;
class SScrollBox;

// The on-screen controls, built in code with Slate:
//   top     score and match clock, and under it the coaching moment on screen
//   left    players (click to follow, boost bars) and the match picker
//   right   coaching moments for the focused player (click to jump there)
//   bottom  transport buttons, speed, camera mode and the timeline
//   world   name tags over the cars (SRLWorldLabels)
// Empty screen space passes clicks through, so right-drag orbiting still works.
class SRLReplayHud : public SCompoundWidget
{
public:
	SLATE_BEGIN_ARGS(SRLReplayHud) {}
		SLATE_ARGUMENT(ARLReplayDirector*, Director)
		SLATE_ARGUMENT(ARLViewerPawn*, Pawn)
		SLATE_ARGUMENT(ARLViewerGameMode*, GameMode)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

private:
	TSharedRef<SWidget> MakeTopBar();
	TSharedRef<SWidget> MakeLeftPanel();
	TSharedRef<SWidget> MakeRightPanel();
	TSharedRef<SWidget> MakeBottomBar();
	TSharedRef<SWidget> MakeMomentCallout();
	TSharedRef<SWidget> MakeButton(const FText& Label, const FText& Tooltip, TFunction<void()> OnClick);

	bool HasReplay() const;
	EVisibility GetHudVisibility() const;
	EActiveTimerReturnType ScrollToActiveMoment(double CurrentTime, float DeltaTime);

	TWeakObjectPtr<ARLReplayDirector> Director;
	TWeakObjectPtr<ARLViewerPawn> Pawn;
	TWeakObjectPtr<ARLViewerGameMode> GameMode;

	// The moments list, kept scrolled to the moment being shown.
	TSharedPtr<SScrollBox> MomentList;
	TArray<TSharedPtr<SWidget>> MomentRows;
	int32 ScrolledToMoment = INDEX_NONE;
};
