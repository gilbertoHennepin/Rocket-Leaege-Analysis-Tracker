#include "SRLWorldLabels.h"

#include "Camera/PlayerCameraManager.h"
#include "Fonts/FontMeasure.h"
#include "Framework/Application/SlateApplication.h"
#include "GameFramework/PlayerController.h"
#include "RLReplayDirector.h"
#include "RLShapes.h"
#include "RLViewerPawn.h"
#include "Rendering/DrawElements.h"
#include "Styling/CoreStyle.h"

namespace
{
constexpr float TagHeightAboveCar = 90.0f;  // uu, so the tag clears the car's roof
const FVector2D TagPadding(6.0f, 2.0f);
const FLinearColor TagBackground(0.0f, 0.0f, 0.0f, 0.72f);

struct FTag
{
	FString Text;
	FSlateFontInfo Font;
	FLinearColor TextColor;
	FLinearColor Background;
	FVector2D TextSize;
	FVector2D Position;  // top-left of the box, in local units
	double Distance = 0.0;

	FVector2D BoxSize() const { return TextSize + TagPadding * 2.0f; }
	FSlateRect Rect() const { return FSlateRect(Position, Position + BoxSize()); }
};
}  // namespace

void SRLWorldLabels::Construct(const FArguments& InArgs)
{
	Director = InArgs._Director;
	Pawn = InArgs._Pawn;
	SetVisibility(EVisibility::HitTestInvisible);
}

int32 SRLWorldLabels::OnPaint(const FPaintArgs& Args, const FGeometry& AllottedGeometry, const FSlateRect& MyCullingRect,
	FSlateWindowElementList& OutDrawElements, int32 LayerId, const FWidgetStyle& InWidgetStyle, bool bParentEnabled) const
{
	const ARLReplayDirector* D = Director.Get();
	const APlayerController* PC = Pawn.IsValid() ? Cast<APlayerController>(Pawn->GetController()) : nullptr;
	if (!D || !PC || !PC->PlayerCameraManager || !D->HasReplay())
	{
		return LayerId;
	}

	const FRLReplay& Replay = D->GetReplay();
	const TSharedRef<FSlateFontMeasure> Measure = FSlateApplication::Get().GetRenderer()->GetFontMeasureService();
	const FVector Camera = PC->PlayerCameraManager->GetCameraLocation();
	// Projection gives viewport pixels; the widget lays out in DPI-scaled units.
	const float PixelsPerUnit = FMath::Max(AllottedGeometry.Scale, KINDA_SMALL_NUMBER);

	TArray<FTag> Tags;
	for (int32 I = 0; I < Replay.Players.Num(); ++I)
	{
		const FVector Car = D->GetCarLocation(I);
		FVector2D Pixel;
		if (!PC->ProjectWorldLocationToScreen(Car + FVector(0, 0, TagHeightAboveCar), Pixel, true))
		{
			continue;  // behind the camera
		}
		const bool bFocus = I == D->GetFocusPlayer();
		const int32 Moment = D->FindMomentAt(D->GetClock().GetTime(), I);

		FTag& Tag = Tags.AddDefaulted_GetRef();
		Tag.Text = FString::Printf(TEXT("%s  %d"), *Replay.Players[I].Name, FMath::RoundToInt(D->GetCarBoost(I)));
		Tag.Font = FCoreStyle::GetDefaultFontStyle(bFocus ? "Bold" : "Regular", bFocus ? 12 : 10);
		Tag.TextColor = RLShapes::Colors::TeamText(Replay.Players[I].Team);
		Tag.Background = TagBackground;
		if (Moment != INDEX_NONE)
		{
			// The player in the coaching callout: a severity-colored tag with dark text.
			Tag.Background = RLShapes::Colors::SeverityText(Replay.Moments[Moment].Severity);
			Tag.TextColor = FLinearColor(0.01f, 0.01f, 0.01f);
		}
		Tag.TextSize = Measure->Measure(Tag.Text, Tag.Font);
		Tag.Position = Pixel / PixelsPerUnit - FVector2D(Tag.BoxSize().X / 2.0f, Tag.BoxSize().Y);
		Tag.Distance = FVector::Dist(Camera, Car);
	}

	// The nearest car keeps its spot; farther tags move up until they're clear.
	Tags.Sort([](const FTag& A, const FTag& B) { return A.Distance < B.Distance; });
	TArray<FSlateRect> Placed;
	for (FTag& Tag : Tags)
	{
		for (int32 Attempt = 0; Attempt < Tags.Num(); ++Attempt)
		{
			const FSlateRect Mine = Tag.Rect();
			const FSlateRect* Hit = Placed.FindByPredicate(
				[&Mine](const FSlateRect& Other) { return FSlateRect::DoRectanglesIntersect(Mine, Other); });
			if (!Hit)
			{
				break;
			}
			Tag.Position.Y = Hit->Top - Tag.BoxSize().Y - 2.0f;
		}
		Placed.Add(Tag.Rect());
	}

	const FSlateBrush* Box = FCoreStyle::Get().GetBrush("GenericWhiteBox");
	for (const FTag& Tag : Tags)
	{
		FSlateDrawElement::MakeBox(OutDrawElements, LayerId,
			AllottedGeometry.ToPaintGeometry(Tag.BoxSize(), FSlateLayoutTransform(Tag.Position)), Box,
			ESlateDrawEffect::None, Tag.Background);
		FSlateDrawElement::MakeText(OutDrawElements, LayerId + 1,
			AllottedGeometry.ToPaintGeometry(Tag.TextSize, FSlateLayoutTransform(Tag.Position + TagPadding)), Tag.Text,
			Tag.Font, ESlateDrawEffect::None, Tag.TextColor);
	}
	return LayerId + 2;
}
