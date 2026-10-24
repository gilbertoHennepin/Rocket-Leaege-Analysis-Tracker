#pragma once

#include "CoreMinimal.h"
#include "Widgets/SCompoundWidget.h"

class ARLReplayDirector;
class ARLViewerGameMode;
class ARLViewerPawn;
class SVerticalBox;

// The on-screen controls, built in code with Slate:
//   top     score and match clock
//   left    players (click to follow, boost bars) and the match picker
//   right   coaching moments for the focused player (click to jump there)
//   bottom  transport buttons, speed, camera mode and the timeline
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
	TSharedRef<SWidget> MakeButton(const FText& Label, const FText& Tooltip, TFunction<void()> OnClick);

	bool HasReplay() const;
	EVisibility GetHudVisibility() const;

	TWeakObjectPtr<ARLReplayDirector> Director;
	TWeakObjectPtr<ARLViewerPawn> Pawn;
	TWeakObjectPtr<ARLViewerGameMode> GameMode;
};
