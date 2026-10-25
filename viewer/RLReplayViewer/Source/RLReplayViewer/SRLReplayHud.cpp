#include "SRLReplayHud.h"

#include "Misc/Paths.h"
#include "RLReplayDirector.h"
#include "RLShapes.h"
#include "RLViewerGameMode.h"
#include "RLViewerPawn.h"
#include "SRLWorldLabels.h"
#include "Styling/CoreStyle.h"
#include "TimerManager.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Input/SSlider.h"
#include "Widgets/Layout/SBorder.h"
#include "Widgets/Layout/SBox.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/Notifications/SProgressBar.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/SNullWidget.h"
#include "Widgets/SOverlay.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "RLReplayHud"

namespace
{
// Nearly opaque, so text keeps its contrast whatever is behind a panel (sky or field).
const FLinearColor PanelColor(0.008f, 0.008f, 0.008f, 0.95f);
const FLinearColor TextColor(0.92f, 0.91f, 0.88f);
const FLinearColor MutedText(0.62f, 0.61f, 0.58f);
const FLinearColor Highlight(1.0f, 1.0f, 1.0f, 0.22f);
const FLinearColor ButtonColor(1.0f, 1.0f, 1.0f, 0.08f);

const FSlateBrush* WhiteBrush()
{
	return FCoreStyle::Get().GetBrush("GenericWhiteBox");
}

FSlateFontInfo Font(int32 Size, bool bBold = false)
{
	return FCoreStyle::GetDefaultFontStyle(bBold ? "Bold" : "Regular", Size);
}

TSharedRef<SWidget> Panel(const TSharedRef<SWidget>& Content)
{
	return SNew(SBorder)
		.BorderImage(WhiteBrush())
		.BorderBackgroundColor(PanelColor)
		.Padding(FMargin(12.0f, 10.0f))
		[Content];
}

TSharedRef<STextBlock> Heading(const FText& Text)
{
	return SNew(STextBlock).Text(Text).Font(Font(11, true)).ColorAndOpacity(MutedText);
}

// "2v2 - Ranked Doubles" from the export's mode and playlist.
FText MatchTitle(const FRLReplay& Replay)
{
	if (Replay.Playlist.IsEmpty())
	{
		return FText::FromString(Replay.Mode);
	}
	return FText::FromString(FString::Printf(TEXT("%s \u00B7 %s"), *Replay.Mode, *Replay.Playlist));
}

FString ReplayLabel(const FString& Path)
{
	FString Name = FPaths::GetCleanFilename(Path);
	Name.RemoveFromEnd(TEXT(".rlview.json"));
	return Name;
}
}  // namespace

void SRLReplayHud::Construct(const FArguments& InArgs)
{
	Director = InArgs._Director;
	Pawn = InArgs._Pawn;
	GameMode = InArgs._GameMode;

	// SelfHitTestInvisible: the panels take clicks, the empty middle of the
	// screen does not, so mouse input there still reaches the camera.
	SetVisibility(TAttribute<EVisibility>::CreateSP(this, &SRLReplayHud::GetHudVisibility));

	ChildSlot
	[
		SNew(SOverlay)
		+ SOverlay::Slot()
		[
			SNew(SRLWorldLabels).Director(InArgs._Director).Pawn(InArgs._Pawn)
		]
		+ SOverlay::Slot().HAlign(HAlign_Center).VAlign(VAlign_Top).Padding(0.0f, 16.0f)
		[
			SNew(SVerticalBox)
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center)
			[
				MakeTopBar()
			]
			+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center).Padding(0.0f, 8.0f, 0.0f, 0.0f)
			[
				MakeMomentCallout()
			]
		]
		+ SOverlay::Slot().HAlign(HAlign_Left).VAlign(VAlign_Top).Padding(16.0f, 16.0f)
		[
			MakeLeftPanel()
		]
		+ SOverlay::Slot().HAlign(HAlign_Right).VAlign(VAlign_Top).Padding(16.0f, 16.0f)
		[
			MakeRightPanel()
		]
		+ SOverlay::Slot().HAlign(HAlign_Fill).VAlign(VAlign_Bottom).Padding(16.0f, 16.0f)
		[
			MakeBottomBar()
		]
	];

	RegisterActiveTimer(0.1f, FWidgetActiveTimerDelegate::CreateSP(this, &SRLReplayHud::ScrollToActiveMoment));
}

EActiveTimerReturnType SRLReplayHud::ScrollToActiveMoment(double CurrentTime, float DeltaTime)
{
	const int32 Active = Director.IsValid() ? Director->GetActiveMoment() : INDEX_NONE;
	if (Active != ScrolledToMoment && MomentList.IsValid() && MomentRows.IsValidIndex(Active)
		&& MomentRows[Active]->GetVisibility().IsVisible())
	{
		MomentList->ScrollDescendantIntoView(MomentRows[Active], true, EDescendantScrollDestination::Center);
		ScrolledToMoment = Active;
	}
	return EActiveTimerReturnType::Continue;
}

bool SRLReplayHud::HasReplay() const
{
	return Director.IsValid() && Director->HasReplay();
}

EVisibility SRLReplayHud::GetHudVisibility() const
{
	const bool bVisible = !Pawn.IsValid() || Pawn->IsHudVisible();
	return bVisible ? EVisibility::SelfHitTestInvisible : EVisibility::Collapsed;
}

TSharedRef<SWidget> SRLReplayHud::MakeButton(const FText& Label, const FText& Tooltip, TFunction<void()> OnClick)
{
	return SNew(SButton)
		.IsFocusable(false)  // keep keyboard focus on the game so Space etc. keep working
		.ButtonColorAndOpacity(ButtonColor)
		.ContentPadding(FMargin(10.0f, 4.0f))
		.ToolTipText(Tooltip)
		.OnClicked_Lambda([OnClick]() {
			OnClick();
			return FReply::Handled();
		})
		[
			SNew(STextBlock).Text(Label).Font(Font(13, true)).ColorAndOpacity(TextColor)
		];
}

TSharedRef<SWidget> SRLReplayHud::MakeTopBar()
{
	if (!HasReplay())
	{
		return SNullWidget::NullWidget;
	}

	TWeakObjectPtr<ARLReplayDirector> WeakDirector = Director;

	// Score counts goals up to the current replay time, so it changes as the match plays.
	auto ScoreFor = [WeakDirector](int32 Team) {
		return TAttribute<FText>::CreateLambda([WeakDirector, Team]() {
			int32 Goals = 0;
			if (WeakDirector.IsValid())
			{
				const double Now = WeakDirector->GetClock().GetTime();
				for (const FRLGoal& Goal : WeakDirector->GetReplay().Goals)
				{
					Goals += (Goal.Team == Team && Goal.Time <= Now) ? 1 : 0;
				}
			}
			return FText::AsNumber(Goals);
		});
	};

	return Panel(
		SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center)
		[
			SNew(SHorizontalBox)
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0.0f, 0.0f, 14.0f, 0.0f)
			[
				SNew(STextBlock).Text(LOCTEXT("Blue", "BLUE")).Font(Font(14, true))
					.ColorAndOpacity(RLShapes::Colors::BlueText)
			]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
			[
				SNew(STextBlock).Text(ScoreFor(0)).Font(Font(26, true)).ColorAndOpacity(TextColor)
			]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(18.0f, 0.0f)
			[
				SNew(STextBlock)
					.Text_Lambda([WeakDirector]() {
						return WeakDirector.IsValid() ? FText::FromString(WeakDirector->GetClockText()) : FText::GetEmpty();
					})
					.Font(Font(22, true))
					.ColorAndOpacity(TextColor)
			]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center)
			[
				SNew(STextBlock).Text(ScoreFor(1)).Font(Font(26, true)).ColorAndOpacity(TextColor)
			]
			+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(14.0f, 0.0f, 0.0f, 0.0f)
			[
				SNew(STextBlock).Text(LOCTEXT("Orange", "ORANGE")).Font(Font(14, true))
					.ColorAndOpacity(RLShapes::Colors::OrangeText)
			]
		]
		+ SVerticalBox::Slot().AutoHeight().HAlign(HAlign_Center).Padding(0.0f, 2.0f, 0.0f, 0.0f)
		[
			SNew(STextBlock).Text(MatchTitle(Director->GetReplay())).Font(Font(10)).ColorAndOpacity(MutedText)
		]);
}

TSharedRef<SWidget> SRLReplayHud::MakeLeftPanel()
{
	TSharedRef<SVerticalBox> Box = SNew(SVerticalBox);
	TWeakObjectPtr<ARLReplayDirector> WeakDirector = Director;
	TWeakObjectPtr<ARLViewerPawn> WeakPawn = Pawn;

	if (GameMode.IsValid() && !GameMode->GetStatusMessage().IsEmpty())
	{
		Box->AddSlot().AutoHeight().Padding(0.0f, 0.0f, 0.0f, 10.0f)
		[
			SNew(STextBlock)
				.Text(FText::FromString(GameMode->GetStatusMessage()))
				.Font(Font(12))
				.ColorAndOpacity(RLShapes::Colors::SeverityText(TEXT("medium")))
				.AutoWrapText(true)
		];
	}

	if (HasReplay())
	{
		Box->AddSlot().AutoHeight().Padding(0.0f, 0.0f, 0.0f, 4.0f)
		[
			Heading(LOCTEXT("Players", "PLAYERS  (1-8 to follow)"))
		];

		const TArray<FRLPlayerInfo>& Players = Director->GetReplay().Players;
		for (int32 I = 0; I < Players.Num(); ++I)
		{
			const FRLPlayerInfo& Player = Players[I];
			const FLinearColor Team = RLShapes::Colors::TeamText(Player.Team);

			Box->AddSlot().AutoHeight().Padding(0.0f, 2.0f)
			[
				SNew(SButton)
					.IsFocusable(false)
					.ContentPadding(FMargin(8.0f, 4.0f))
					.ToolTipText(LOCTEXT("FollowTip", "Follow this player in ball cam"))
					.ButtonColorAndOpacity_Lambda([WeakDirector, I]() {
						const bool bFocused = WeakDirector.IsValid() && WeakDirector->GetFocusPlayer() == I;
						return FSlateColor(bFocused ? Highlight : ButtonColor);
					})
					.OnClicked_Lambda([WeakPawn, I]() {
						if (WeakPawn.IsValid())
						{
							WeakPawn->FocusPlayer(I);
						}
						return FReply::Handled();
					})
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(SHorizontalBox)
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(0.0f, 0.0f, 8.0f, 0.0f)
							[
								SNew(STextBlock)
									.Text(FText::AsNumber(I + 1))
									.Font(Font(12, true))
									.ColorAndOpacity(MutedText)
							]
							+ SHorizontalBox::Slot().FillWidth(1.0f).VAlign(VAlign_Center)
							[
								SNew(STextBlock).Text(FText::FromString(Player.Name)).Font(Font(13, true)).ColorAndOpacity(Team)
							]
							+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Center).Padding(8.0f, 0.0f, 0.0f, 0.0f)
							[
								SNew(STextBlock)
									.Text_Lambda([WeakDirector, I]() {
										const double Boost = WeakDirector.IsValid() ? WeakDirector->GetCarBoost(I) : 0.0;
										return FText::AsNumber(FMath::RoundToInt(Boost));
									})
									.Font(Font(12))
									.ColorAndOpacity(TextColor)
							]
						]
						+ SVerticalBox::Slot().AutoHeight().Padding(0.0f, 3.0f, 0.0f, 0.0f)
						[
							SNew(SBox)
								.HeightOverride(5.0f)
								[
									SNew(SProgressBar)
										.Percent_Lambda([WeakDirector, I]() -> TOptional<float> {
											const double Boost = WeakDirector.IsValid() ? WeakDirector->GetCarBoost(I) : 0.0;
											return static_cast<float>(Boost / 100.0);
										})
										.FillColorAndOpacity(RLShapes::Colors::Boost)
								]
						]
					]
			];
		}
	}

	// Match picker: every export in the Replays folder, newest first.
	const TArray<FString> Files = ARLViewerGameMode::FindReplayFiles();
	if (Files.Num() > 0)
	{
		Box->AddSlot().AutoHeight().Padding(0.0f, 14.0f, 0.0f, 4.0f)
		[
			Heading(LOCTEXT("Matches", "MATCHES"))
		];

		TSharedRef<SScrollBox> List = SNew(SScrollBox);
		const FString Loaded = Director.IsValid() ? Director->GetLoadedPath() : FString();
		TWeakObjectPtr<ARLViewerGameMode> WeakGameMode = GameMode;
		for (const FString& Path : Files)
		{
			const bool bLoaded = FPaths::IsSamePath(Path, Loaded);
			List->AddSlot().Padding(0.0f, 1.0f)
			[
				SNew(SButton)
					.IsFocusable(false)
					.ContentPadding(FMargin(8.0f, 3.0f))
					.ButtonColorAndOpacity(bLoaded ? Highlight : ButtonColor)
					.OnClicked_Lambda([WeakGameMode, Path]() {
						// Opening a match rebuilds this HUD, so wait until the
						// click has finished being handled.
						if (WeakGameMode.IsValid())
						{
							WeakGameMode->GetWorldTimerManager().SetTimerForNextTick([WeakGameMode, Path]() {
								if (WeakGameMode.IsValid())
								{
									WeakGameMode->OpenReplay(Path);
								}
							});
						}
						return FReply::Handled();
					})
					[
						SNew(STextBlock)
							.Text(FText::FromString(ReplayLabel(Path)))
							.Font(Font(11, bLoaded))
							.ColorAndOpacity(bLoaded ? TextColor : MutedText)
					]
			];
		}
		Box->AddSlot().AutoHeight()
		[
			SNew(SBox).MaxDesiredHeight(220.0f)[List]
		];
	}

	return SNew(SBox).WidthOverride(280.0f)[Panel(Box)];
}

TSharedRef<SWidget> SRLReplayHud::MakeRightPanel()
{
	if (!HasReplay())
	{
		return SNullWidget::NullWidget;
	}

	TWeakObjectPtr<ARLReplayDirector> WeakDirector = Director;
	const FRLReplay& Replay = Director->GetReplay();
	TSharedRef<SScrollBox> List = SNew(SScrollBox);
	MomentList = List;
	MomentRows.Reset();

	for (int32 I = 0; I < Replay.Moments.Num(); ++I)
	{
		const FRLMoment& Moment = Replay.Moments[I];
		const int32 PlayerIndex = Replay.FindPlayer(Moment.PlayerId);
		const FString PlayerName = Replay.Players.IsValidIndex(PlayerIndex) ? Replay.Players[PlayerIndex].Name : FString();
		const FLinearColor Severity = RLShapes::Colors::SeverityText(Moment.Severity);
		const FString Title = Moment.bPunished ? Moment.Title + TEXT("  (punished)") : Moment.Title;

		TSharedPtr<SWidget> Row;
		List->AddSlot().Padding(0.0f, 2.0f)
		[
			SAssignNew(Row, SButton)
				.IsFocusable(false)
				.ContentPadding(FMargin(8.0f, 4.0f))
				.ToolTipText(FText::FromString(Moment.Detail))
				// Only the focused player's moments, or everyone's when nobody is focused.
				.Visibility_Lambda([WeakDirector, PlayerIndex]() {
					const int32 Focus = WeakDirector.IsValid() ? WeakDirector->GetFocusPlayer() : INDEX_NONE;
					return (Focus == INDEX_NONE || Focus == PlayerIndex) ? EVisibility::Visible : EVisibility::Collapsed;
				})
				.ButtonColorAndOpacity_Lambda([WeakDirector, I]() {
					const bool bActive = WeakDirector.IsValid() && WeakDirector->GetActiveMoment() == I;
					return FSlateColor(bActive ? Highlight : ButtonColor);
				})
				.OnClicked_Lambda([WeakDirector, I]() {
					if (WeakDirector.IsValid())
					{
						WeakDirector->JumpToMoment(I);
					}
					return FReply::Handled();
				})
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth().VAlign(VAlign_Top).Padding(0.0f, 0.0f, 10.0f, 0.0f)
					[
						SNew(STextBlock).Text(FText::FromString(Moment.Clock)).Font(Font(12, true)).ColorAndOpacity(MutedText)
					]
					+ SHorizontalBox::Slot().FillWidth(1.0f)
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(STextBlock)
								.Text(FText::FromString(Title))
								.Font(Font(12, true))
								.ColorAndOpacity(Severity)
								.AutoWrapText(true)
						]
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(STextBlock).Text(FText::FromString(PlayerName)).Font(Font(10)).ColorAndOpacity(MutedText)
						]
					]
				]
		];
		MomentRows.Add(Row);
	}

	TSharedRef<SVerticalBox> Box = SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight().Padding(0.0f, 0.0f, 0.0f, 4.0f)
		[
			Heading(LOCTEXT("Moments", "COACHING MOMENTS  (N / P)"))
		];

	if (Replay.Moments.Num() == 0)
	{
		Box->AddSlot().AutoHeight()
		[
			SNew(STextBlock)
				.Text(LOCTEXT("NoMoments", "No coaching moments in this match."))
				.Font(Font(11))
				.ColorAndOpacity(MutedText)
		];
	}
	else
	{
		Box->AddSlot().AutoHeight()
		[
			SNew(SBox).MaxDesiredHeight(520.0f)[List]
		];
	}

	return SNew(SBox).WidthOverride(320.0f)[Panel(Box)];
}

TSharedRef<SWidget> SRLReplayHud::MakeMomentCallout()
{
	if (!HasReplay())
	{
		return SNullWidget::NullWidget;
	}

	// The coaching moment being shown right now, in a fixed spot under the
	// score where it is easy to read; the player's name tag lights up in the
	// same color to show who it is about.
	TWeakObjectPtr<ARLReplayDirector> WeakDirector = Director;
	auto Active = [WeakDirector]() -> const FRLMoment* {
		if (!WeakDirector.IsValid())
		{
			return nullptr;
		}
		const TArray<FRLMoment>& Moments = WeakDirector->GetReplay().Moments;
		const int32 I = WeakDirector->GetActiveMoment();
		return Moments.IsValidIndex(I) ? &Moments[I] : nullptr;
	};

	return SNew(SBox)
		.WidthOverride(560.0f)
		.Visibility_Lambda([Active]() { return Active() ? EVisibility::HitTestInvisible : EVisibility::Collapsed; })
		[
			SNew(SBorder)
				.BorderImage(WhiteBrush())
				.BorderBackgroundColor(PanelColor)
				.Padding(0.0f)
				[
					SNew(SHorizontalBox)
					+ SHorizontalBox::Slot().AutoWidth()
					[
						SNew(SBorder)
							.BorderImage(WhiteBrush())
							.BorderBackgroundColor_Lambda([Active]() {
								const FRLMoment* M = Active();
								return FSlateColor(M ? RLShapes::Colors::SeverityText(M->Severity) : MutedText);
							})
							.Padding(FMargin(3.0f, 0.0f))
					]
					+ SHorizontalBox::Slot().FillWidth(1.0f).Padding(FMargin(12.0f, 8.0f))
					[
						SNew(SVerticalBox)
						+ SVerticalBox::Slot().AutoHeight()
						[
							SNew(STextBlock)
								.Text_Lambda([Active]() {
									const FRLMoment* M = Active();
									if (!M)
									{
										return FText::GetEmpty();
									}
									return FText::FromString(M->bPunished ? M->Title + TEXT("  \u00B7  punished") : M->Title);
								})
								.ColorAndOpacity_Lambda([Active]() {
									const FRLMoment* M = Active();
									return FSlateColor(M ? RLShapes::Colors::SeverityText(M->Severity) : TextColor);
								})
								.Font(Font(15, true))
								.AutoWrapText(true)
						]
						+ SVerticalBox::Slot().AutoHeight().Padding(0.0f, 2.0f, 0.0f, 0.0f)
						[
							SNew(STextBlock)
								.Text_Lambda([Active, WeakDirector]() {
									const FRLMoment* M = Active();
									if (!M || !WeakDirector.IsValid())
									{
										return FText::GetEmpty();
									}
									const FRLReplay& Replay = WeakDirector->GetReplay();
									const int32 Player = Replay.FindPlayer(M->PlayerId);
									const FString Name = Replay.Players.IsValidIndex(Player) ? Replay.Players[Player].Name : FString();
									return FText::FromString(FString::Printf(TEXT("%s  \u00B7  %s  \u00B7  %s"), *M->Clock, *Name, *M->Detail));
								})
								.ColorAndOpacity(TextColor)
								.Font(Font(11))
								.AutoWrapText(true)
						]
					]
				]
		];
}

TSharedRef<SWidget> SRLReplayHud::MakeBottomBar()
{
	if (!HasReplay())
	{
		return SNullWidget::NullWidget;
	}

	TWeakObjectPtr<ARLReplayDirector> WeakDirector = Director;
	TWeakObjectPtr<ARLViewerPawn> WeakPawn = Pawn;
	auto WithClock = [WeakDirector](TFunction<void(rlcore::PlaybackClock&)> Fn) {
		return [WeakDirector, Fn]() {
			if (WeakDirector.IsValid())
			{
				Fn(WeakDirector->GetClock());
			}
		};
	};

	TSharedRef<SHorizontalBox> Controls = SNew(SHorizontalBox);
	auto AddControl = [&Controls](const TSharedRef<SWidget>& Widget) {
		Controls->AddSlot().AutoWidth().VAlign(VAlign_Center).Padding(0.0f, 0.0f, 6.0f, 0.0f)[Widget];
	};

	AddControl(MakeButton(LOCTEXT("Prev", "|<"), LOCTEXT("PrevTip", "Previous coaching moment (P)"), [WeakDirector]() {
		if (WeakDirector.IsValid())
		{
			WeakDirector->JumpToPreviousMoment();
		}
	}));
	AddControl(MakeButton(LOCTEXT("Back", "-5s"), LOCTEXT("BackTip", "Back 5 seconds (Left)"),
		WithClock([](rlcore::PlaybackClock& Clock) { Clock.Step(-5.0); })));

	// Play/pause shows its current state, so it's built by hand rather than with MakeButton.
	AddControl(SNew(SButton)
		.IsFocusable(false)
		.ButtonColorAndOpacity(ButtonColor)
		.ContentPadding(FMargin(14.0f, 4.0f))
		.ToolTipText(LOCTEXT("PlayTip", "Play / pause (Space)"))
		.OnClicked_Lambda([WeakDirector]() {
			if (WeakDirector.IsValid())
			{
				WeakDirector->GetClock().TogglePlay();
			}
			return FReply::Handled();
		})
		[
			SNew(STextBlock)
				.Text_Lambda([WeakDirector]() {
					const bool bPlaying = WeakDirector.IsValid() && WeakDirector->GetClock().IsPlaying();
					return bPlaying ? LOCTEXT("Pause", "Pause") : LOCTEXT("Play", "Play");
				})
				.Font(Font(13, true))
				.ColorAndOpacity(TextColor)
		]);

	AddControl(MakeButton(LOCTEXT("Fwd", "+5s"), LOCTEXT("FwdTip", "Forward 5 seconds (Right)"),
		WithClock([](rlcore::PlaybackClock& Clock) { Clock.Step(5.0); })));
	AddControl(MakeButton(LOCTEXT("Next", ">|"), LOCTEXT("NextTip", "Next coaching moment (N)"), [WeakDirector]() {
		if (WeakDirector.IsValid())
		{
			WeakDirector->JumpToNextMoment();
		}
	}));
	AddControl(MakeButton(LOCTEXT("Slower", "-"), LOCTEXT("SlowerTip", "Slower (Down)"),
		WithClock([](rlcore::PlaybackClock& Clock) { Clock.Slower(); })));
	AddControl(SNew(SBox).WidthOverride(48.0f).HAlign(HAlign_Center)
		[
			SNew(STextBlock)
				.Text_Lambda([WeakDirector]() {
					const double Speed = WeakDirector.IsValid() ? WeakDirector->GetClock().GetSpeed() : 1.0;
					return FText::FromString(FString::Printf(TEXT("%gx"), Speed));
				})
				.Font(Font(13, true))
				.ColorAndOpacity(TextColor)
		]);
	AddControl(MakeButton(LOCTEXT("Faster", "+"), LOCTEXT("FasterTip", "Faster (Up)"),
		WithClock([](rlcore::PlaybackClock& Clock) { Clock.Faster(); })));

	AddControl(SNew(SButton)
		.IsFocusable(false)
		.ButtonColorAndOpacity(ButtonColor)
		.ContentPadding(FMargin(10.0f, 4.0f))
		.ToolTipText(LOCTEXT("CameraTip", "Change camera (C)"))
		.OnClicked_Lambda([WeakPawn]() {
			if (WeakPawn.IsValid())
			{
				WeakPawn->CycleCameraMode();
			}
			return FReply::Handled();
		})
		[
			SNew(STextBlock)
				.Text_Lambda([WeakPawn]() {
					return WeakPawn.IsValid() ? WeakPawn->GetCameraModeText() : FText::GetEmpty();
				})
				.Font(Font(13, true))
				.ColorAndOpacity(TextColor)
		]);

	Controls->AddSlot().FillWidth(1.0f).HAlign(HAlign_Right).VAlign(VAlign_Center)
	[
		SNew(STextBlock)
			.Text(LOCTEXT("Help", "Space play  \u00B7  arrows seek / speed  \u00B7  N/P moments  \u00B7  1-8 follow  \u00B7  C camera  \u00B7  right-drag orbit  \u00B7  H hide"))
			.Font(Font(10))
			.ColorAndOpacity(MutedText)
	];

	return Panel(
		SNew(SVerticalBox)
		+ SVerticalBox::Slot().AutoHeight()
		[
			Controls
		]
		+ SVerticalBox::Slot().AutoHeight().Padding(0.0f, 8.0f, 0.0f, 0.0f)
		[
			SNew(SSlider)
				.IsFocusable(false)
				.SliderBarColor(FLinearColor(1.0f, 1.0f, 1.0f, 0.25f))
				.SliderHandleColor(TextColor)
				.Value_Lambda([WeakDirector]() {
					return WeakDirector.IsValid() ? static_cast<float>(WeakDirector->GetClock().GetProgress()) : 0.0f;
				})
				.OnValueChanged_Lambda([WeakDirector](float Value) {
					if (WeakDirector.IsValid())
					{
						WeakDirector->GetClock().SeekProgress(Value);
					}
				})
		]);
}

#undef LOCTEXT_NAMESPACE
