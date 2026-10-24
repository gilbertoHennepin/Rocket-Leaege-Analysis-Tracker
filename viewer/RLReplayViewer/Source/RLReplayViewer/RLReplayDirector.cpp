#include "RLReplayDirector.h"

#include "Camera/PlayerCameraManager.h"
#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Components/TextRenderComponent.h"
#include "DrawDebugHelpers.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "RLReplayViewer.h"
#include "RLShapes.h"

namespace
{
FVector ToFVector(const rlcore::Vec3& V)
{
	return FVector(V.X, V.Y, V.Z);
}

FQuat ToFQuat(const rlcore::Quat& Q)
{
	// Rocket League replays use Unreal's coordinate system, so the replay
	// quaternion maps straight onto FQuat.
	return FQuat(Q.X, Q.Y, Q.Z, Q.W);
}

// Octane hitbox, the most common car body.
const FVector CarSize(118.0, 84.2, 36.2);
constexpr double BallDiameter = 185.5;

// How long a moment's label stays up around the moment itself.
constexpr double MomentShowBefore = 0.75;
constexpr double MomentShowAfter = 2.5;
}  // namespace

ARLReplayDirector::ARLReplayDirector()
{
	PrimaryActorTick.bCanEverTick = true;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

bool ARLReplayDirector::LoadReplay(const FString& Path, FString& OutError)
{
	FRLReplay Loaded;
	if (!FRLReplay::LoadFromFile(Path, Loaded, OutError))
	{
		UE_LOG(LogRLViewer, Warning, TEXT("Failed to load %s: %s"), *Path, *OutError);
		return false;
	}

	ClearVisuals();
	Replay = MoveTemp(Loaded);
	LoadedPath = Path;
	MomentTimes = Replay.MomentTimes();
	Clock = rlcore::PlaybackClock(Replay.StartTime(), Replay.EndTime());
	FocusPlayer = Replay.FindPlayer(Replay.RecordedBy);
	ActiveMoment = INDEX_NONE;
	BuildVisuals();
	UpdateVisuals();
	Clock.Play();

	UE_LOG(LogRLViewer, Log, TEXT("Loaded %s: %s, %d players, %d frames, %d coaching moments"), *Path, *Replay.Mode,
		Replay.Players.Num(), Replay.NumFrames(), Replay.Moments.Num());
	return true;
}

void ARLReplayDirector::ClearVisuals()
{
	for (USceneComponent* Component : SpawnedComponents)
	{
		if (Component)
		{
			Component->DestroyComponent();
		}
	}
	SpawnedComponents.Reset();
	Cars.Reset();
	BallMesh = nullptr;
}

void ARLReplayDirector::BuildVisuals()
{
	using namespace RLShapes;

	BallMesh = AddShape(this, Sphere(), FVector::ZeroVector, FVector(BallDiameter), MakeMaterial(this, Colors::Ball));
	SpawnedComponents.Add(BallMesh);

	for (const FRLPlayerInfo& Player : Replay.Players)
	{
		FCarVisual& Car = Cars.AddDefaulted_GetRef();

		Car.Root = NewObject<USceneComponent>(this);
		Car.Root->SetMobility(EComponentMobility::Movable);
		Car.Root->SetupAttachment(RootComponent);
		Car.Root->RegisterComponent();
		AddInstanceComponent(Car.Root);
		SpawnedComponents.Add(Car.Root);

		const FLinearColor TeamColor = Colors::Team(Player.Team);
		Car.Body = AddShape(this, Cube(), FVector(14, 0, 4), CarSize, MakeMaterial(this, TeamColor), FRotator::ZeroRotator, Car.Root);
		// A darker cone on the nose shows which way the car is facing.
		UStaticMeshComponent* Nose = AddShape(this, Cone(), FVector(14 + CarSize.X / 2 + 12, 0, 4), FVector(40, 40, 30),
			MakeMaterial(this, TeamColor * 0.4f), FRotator(-90, 0, 0), Car.Root);
		SpawnedComponents.Add(Car.Body);
		SpawnedComponents.Add(Nose);

		auto MakeLabel = [this](float Size) {
			UTextRenderComponent* Label = NewObject<UTextRenderComponent>(this);
			Label->SetupAttachment(RootComponent);
			Label->SetUsingAbsoluteLocation(true);
			Label->SetUsingAbsoluteRotation(true);
			Label->SetHorizontalAlignment(EHTA_Center);
			Label->SetVerticalAlignment(EVRTA_TextCenter);
			Label->SetWorldSize(Size);
			Label->SetCastShadow(false);
			Label->RegisterComponent();
			AddInstanceComponent(Label);
			SpawnedComponents.Add(Label);
			return Label;
		};
		Car.Label = MakeLabel(48.0f);
		Car.Label->SetTextRenderColor(TeamColor.ToFColor(true));
		Car.MomentLabel = MakeLabel(56.0f);
		Car.MomentLabel->SetVisibility(false);
	}
}

void ARLReplayDirector::SetFocusPlayer(int32 PlayerIndex)
{
	FocusPlayer = Replay.Players.IsValidIndex(PlayerIndex) ? PlayerIndex : INDEX_NONE;
	for (FCarVisual& Car : Cars)
	{
		Car.ShownBoost = -1;  // force label refresh
	}
}

FVector ARLReplayDirector::GetBallLocation() const
{
	return HasReplay() ? ToFVector(rlcore::SamplePosition(Replay.Ball.Pos, Sample)) : FVector::ZeroVector;
}

FVector ARLReplayDirector::GetCarLocation(int32 PlayerIndex) const
{
	if (!Replay.Cars.IsValidIndex(PlayerIndex))
	{
		return FVector::ZeroVector;
	}
	return ToFVector(rlcore::SamplePosition(Replay.Cars[PlayerIndex].Pos, Sample));
}

double ARLReplayDirector::GetCarBoost(int32 PlayerIndex) const
{
	if (!Replay.Cars.IsValidIndex(PlayerIndex))
	{
		return 0.0;
	}
	const std::vector<double>& Boost = Replay.Cars[PlayerIndex].Boost;
	return Sample.Index0 < Boost.size() ? Boost[Sample.Index0] : 0.0;
}

FString ARLReplayDirector::GetClockText() const
{
	return UTF8_TO_TCHAR(rlcore::FormatClock(Replay.ClockAt(Clock.GetTime())).c_str());
}

void ARLReplayDirector::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	if (!HasReplay())
	{
		return;
	}
	Clock.Tick(DeltaSeconds);
	UpdateVisuals();

	const APlayerController* PC = GetWorld()->GetFirstPlayerController();
	if (PC && PC->PlayerCameraManager)
	{
		UpdateLabels(PC->PlayerCameraManager->GetCameraLocation());
	}
	DrawBallTrail();
}

void ARLReplayDirector::UpdateVisuals()
{
	Sample = rlcore::FindSample(Replay.Times, Clock.GetTime());
	if (BallMesh)
	{
		BallMesh->SetWorldLocationAndRotation(
			ToFVector(rlcore::SamplePosition(Replay.Ball.Pos, Sample)), ToFQuat(rlcore::SampleRotation(Replay.Ball.Rot, Sample)));
	}
	for (int32 I = 0; I < Cars.Num(); ++I)
	{
		const FRLCarTrack& Track = Replay.Cars[I];
		Cars[I].Root->SetWorldLocationAndRotation(
			ToFVector(rlcore::SamplePosition(Track.Pos, Sample)), ToFQuat(rlcore::SampleRotation(Track.Rot, Sample)));
	}
	ActiveMoment = FindMomentAt(Clock.GetTime(), INDEX_NONE);
}

void ARLReplayDirector::UpdateLabels(const FVector& CameraLocation)
{
	for (int32 I = 0; I < Cars.Num(); ++I)
	{
		FCarVisual& Car = Cars[I];
		const FRLPlayerInfo& Player = Replay.Players[I];
		const FVector CarLocation = Car.Root->GetComponentLocation();
		const bool bFocus = I == FocusPlayer;

		// Text renders face +X, so point X at the camera to keep labels readable.
		auto FaceCamera = [&CameraLocation](UTextRenderComponent* Label, const FVector& Location) {
			Label->SetWorldLocationAndRotation(Location, FRotationMatrix::MakeFromX(CameraLocation - Location).Rotator());
		};

		const int32 Boost = FMath::RoundToInt(GetCarBoost(I));
		if (Boost != Car.ShownBoost)
		{
			Car.ShownBoost = Boost;
			const FString Prefix = bFocus ? TEXT("> ") : TEXT("");
			Car.Label->SetText(FText::FromString(FString::Printf(TEXT("%s%s  %d"), *Prefix, *Player.Name, Boost)));
			Car.Label->SetWorldSize(bFocus ? 64.0f : 48.0f);
		}
		FaceCamera(Car.Label, CarLocation + FVector(0, 0, 130));

		const int32 MomentIndex = FindMomentAt(Clock.GetTime(), I);
		Car.MomentLabel->SetVisibility(MomentIndex != INDEX_NONE);
		if (MomentIndex != INDEX_NONE)
		{
			const FRLMoment& M = Replay.Moments[MomentIndex];
			const FString Text = M.bPunished && M.Kind != TEXT("conceded") && M.Kind != TEXT("caught_ahead")
				? FString::Printf(TEXT("%s (punished)"), *M.Title)
				: M.Title;
			Car.MomentLabel->SetText(FText::FromString(Text));
			Car.MomentLabel->SetTextRenderColor(RLShapes::Colors::SeverityColor(M.Severity));
			FaceCamera(Car.MomentLabel, CarLocation + FVector(0, 0, 210));
		}
	}
}

void ARLReplayDirector::DrawBallTrail() const
{
#if ENABLE_DRAW_DEBUG
	// The last 1.5 seconds of ball flight, which makes passes and clears easy to read.
	const int32 TrailFrames = FMath::Max(1, FMath::RoundToInt(1.5 * Replay.Fps));
	const int32 End = static_cast<int32>(Sample.Index0);
	const int32 Begin = FMath::Max(0, End - TrailFrames);
	for (int32 I = Begin; I < End; ++I)
	{
		if (!Replay.Live[I] || !Replay.Live[I + 1])
		{
			continue;
		}
		const FVector A = ToFVector(rlcore::Vec3At(Replay.Ball.Pos, I));
		const FVector B = ToFVector(rlcore::Vec3At(Replay.Ball.Pos, I + 1));
		const uint8 Alpha = static_cast<uint8>(255.0 * (I - Begin + 1) / (End - Begin + 1));
		DrawDebugLine(GetWorld(), A, B, FColor(232, 230, 224, Alpha), false, -1.0f, 0, 6.0f);
	}
#endif
}

int32 ARLReplayDirector::FindMomentAt(double Time, int32 PlayerIndex) const
{
	// The most recent moment whose display window contains Time.
	for (int32 I = Replay.Moments.Num() - 1; I >= 0; --I)
	{
		const FRLMoment& M = Replay.Moments[I];
		if (M.Time - MomentShowBefore > Time)
		{
			continue;
		}
		if (M.Time + MomentShowAfter < Time)
		{
			break;  // sorted by time: everything earlier is out of range too
		}
		if (PlayerIndex == INDEX_NONE || Replay.FindPlayer(M.PlayerId) == PlayerIndex)
		{
			return I;
		}
	}
	return INDEX_NONE;
}

void ARLReplayDirector::JumpToMoment(int32 MomentIndex)
{
	if (!Replay.Moments.IsValidIndex(MomentIndex))
	{
		return;
	}
	const FRLMoment& M = Replay.Moments[MomentIndex];
	Clock.Seek(M.Time - MomentLeadIn);
	Clock.Play();
	SetFocusPlayer(Replay.FindPlayer(M.PlayerId));
	UpdateVisuals();
}

void ARLReplayDirector::JumpToNextMoment()
{
	// Measure from the moment we are previewing, not the lead-in time we seeked to.
	const double From = Clock.GetTime() + MomentLeadIn;
	for (int32 I = rlcore::NextMoment(MomentTimes, From); I >= 0 && I < Replay.Moments.Num(); ++I)
	{
		if (FocusPlayer == INDEX_NONE || Replay.FindPlayer(Replay.Moments[I].PlayerId) == FocusPlayer)
		{
			JumpToMoment(I);
			return;
		}
	}
}

void ARLReplayDirector::JumpToPreviousMoment()
{
	const double From = Clock.GetTime() + MomentLeadIn;
	for (int32 I = rlcore::PrevMoment(MomentTimes, From); I >= 0; --I)
	{
		if (FocusPlayer == INDEX_NONE || Replay.FindPlayer(Replay.Moments[I].PlayerId) == FocusPlayer)
		{
			JumpToMoment(I);
			return;
		}
	}
}
