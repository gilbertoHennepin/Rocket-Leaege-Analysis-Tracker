#pragma once

#include "CoreMinimal.h"
#include "Widgets/SLeafWidget.h"

class ARLReplayDirector;
class ARLViewerPawn;

// Name tags over the cars, drawn in screen space rather than as 3D text: they
// stay the same readable size at any camera distance, never sink into a wall,
// and are nudged apart when cars bunch up. A tag turns the moment's severity
// color while that player has a coaching moment on screen. Takes no input,
// so clicks pass through to the camera.
class SRLWorldLabels : public SLeafWidget
{
public:
	SLATE_BEGIN_ARGS(SRLWorldLabels) {}
		SLATE_ARGUMENT(ARLReplayDirector*, Director)
		SLATE_ARGUMENT(ARLViewerPawn*, Pawn)
	SLATE_END_ARGS()

	void Construct(const FArguments& InArgs);

	virtual int32 OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry, const FSlateRect& MyCullingRect,
		FSlateWindowElementList& OutDrawElements, int32 LayerId, const FWidgetStyle& InWidgetStyle,
		bool bParentEnabled) const override;

protected:
	virtual FVector2D ComputeDesiredSize(float LayoutScaleMultiplier) const override { return FVector2D::ZeroVector; }

private:
	TWeakObjectPtr<ARLReplayDirector> Director;
	TWeakObjectPtr<ARLViewerPawn> Pawn;
};
