#include "RLViewerPawn.h"

#include "Camera/CameraComponent.h"
#include "Components/InputComponent.h"
#include "Core/ReplayCore.h"
#include "GameFramework/PlayerController.h"
#include "InputCoreTypes.h"
#include "RLReplayDirector.h"

namespace
{
rlcore::Vec3 ToVec3(const FVector& V)
{
	return {V.X, V.Y, V.Z};
}

FVector ToFVector(const rlcore::Vec3& V)
{
	return FVector(V.X, V.Y, V.Z);
}

constexpr double MinOrbitDistance = 600.0;
constexpr double MaxOrbitDistance = 9000.0;
constexpr double CameraSmoothing = 6.0;  // higher follows faster
}  // namespace

ARLViewerPawn::ARLViewerPawn()
{
	PrimaryActorTick.bCanEverTick = true;
	// Update after the director has moved the cars this frame.
	PrimaryActorTick.TickGroup = TG_PostUpdateWork;

	Camera = CreateDefaultSubobject<UCameraComponent>(TEXT("Camera"));
	Camera->SetFieldOfView(100.0f);  // close to Rocket League's default FOV
	RootComponent = Camera;
}

void ARLViewerPawn::SetCameraMode(ERLCameraMode NewMode)
{
	Mode = NewMode;
	if (Mode == ERLCameraMode::BallCam && Director.IsValid() && Director->GetFocusPlayer() == INDEX_NONE)
	{
		Director->SetFocusPlayer(0);
	}
}

void ARLViewerPawn::CycleCameraMode()
{
	switch (Mode)
	{
	case ERLCameraMode::Orbit:
		SetCameraMode(ERLCameraMode::BallCam);
		break;
	case ERLCameraMode::BallCam:
		SetCameraMode(ERLCameraMode::TopDown);
		break;
	default:
		SetCameraMode(ERLCameraMode::Orbit);
		break;
	}
}

FText ARLViewerPawn::GetCameraModeText() const
{
	switch (Mode)
	{
	case ERLCameraMode::BallCam:
		return NSLOCTEXT("RLViewer", "BallCam", "Ball cam");
	case ERLCameraMode::TopDown:
		return NSLOCTEXT("RLViewer", "TopDown", "Top down");
	default:
		return NSLOCTEXT("RLViewer", "Orbit", "Orbit");
	}
}

void ARLViewerPawn::Tick(float DeltaSeconds)
{
	Super::Tick(DeltaSeconds);
	PollOrbitKeys(DeltaSeconds);
	if (!Director.IsValid() || !Director->HasReplay())
	{
		return;
	}

	const rlcore::Vec3 Ball = ToVec3(Director->GetBallLocation());
	rlcore::CameraPose Pose;
	switch (Mode)
	{
	case ERLCameraMode::BallCam:
		Pose = Director->GetFocusPlayer() != INDEX_NONE
			? rlcore::BallCam(ToVec3(Director->GetCarLocation(Director->GetFocusPlayer())), Ball)
			: rlcore::Orbit(Ball, OrbitYawDeg, OrbitPitchDeg, OrbitDistance);
		break;
	case ERLCameraMode::TopDown:
		// High enough to fit the whole field with this FOV; yaw so goals are left and right.
		Pose.LookAt = {0.0, 0.0, 0.0};
		Pose.Location = {-1.0, 0.0, 8600.0};
		break;
	default:
		Pose = rlcore::Orbit(Ball, OrbitYawDeg, OrbitPitchDeg, OrbitDistance);
		break;
	}

	if (Mode != ERLCameraMode::TopDown)
	{
		Pose.Location = rlcore::ClampToArena(Pose.Location);  // never behind a wall
	}
	const FVector TargetLocation = ToFVector(Pose.Location);
	const FVector TargetLookAt = ToFVector(Pose.LookAt);
	if (!bHasSmoothed)
	{
		SmoothedLocation = TargetLocation;
		SmoothedLookAt = TargetLookAt;
		bHasSmoothed = true;
	}
	SmoothedLocation = FMath::VInterpTo(SmoothedLocation, TargetLocation, DeltaSeconds, CameraSmoothing);
	SmoothedLookAt = FMath::VInterpTo(SmoothedLookAt, TargetLookAt, DeltaSeconds, CameraSmoothing * 1.5);

	FRotator Rotation = FRotationMatrix::MakeFromX(SmoothedLookAt - SmoothedLocation).Rotator();
	if (Mode == ERLCameraMode::TopDown)
	{
		Rotation = FRotator(-90.0, 0.0, 0.0);  // screen up is +X, so the goals sit left and right
	}
	SetActorLocationAndRotation(SmoothedLocation, Rotation);
	if (AController* PC = GetController())
	{
		PC->SetControlRotation(Rotation);
	}
}

void ARLViewerPawn::SetupPlayerInputComponent(UInputComponent* Input)
{
	Super::SetupPlayerInputComponent(Input);

	// Playback
	Input->BindKey(EKeys::SpaceBar, IE_Pressed, this, &ARLViewerPawn::TogglePlay);
	Input->BindKey(EKeys::Left, IE_Pressed, this, &ARLViewerPawn::StepBack);
	Input->BindKey(EKeys::Right, IE_Pressed, this, &ARLViewerPawn::StepForward);
	Input->BindKey(EKeys::Up, IE_Pressed, this, &ARLViewerPawn::Faster);
	Input->BindKey(EKeys::Down, IE_Pressed, this, &ARLViewerPawn::Slower);
	Input->BindKey(EKeys::N, IE_Pressed, this, &ARLViewerPawn::NextMoment);
	Input->BindKey(EKeys::P, IE_Pressed, this, &ARLViewerPawn::PreviousMoment);

	// Camera
	Input->BindKey(EKeys::C, IE_Pressed, this, &ARLViewerPawn::CycleCameraMode);
	Input->BindKey(EKeys::H, IE_Pressed, this, &ARLViewerPawn::ToggleHud);
	Input->BindKey(EKeys::Zero, IE_Pressed, this, &ARLViewerPawn::ClearFocus);
	Input->BindKey(EKeys::MouseScrollUp, IE_Pressed, this, &ARLViewerPawn::ZoomIn);
	Input->BindKey(EKeys::MouseScrollDown, IE_Pressed, this, &ARLViewerPawn::ZoomOut);
	Input->BindKey(EKeys::E, IE_Pressed, this, &ARLViewerPawn::ZoomIn);
	Input->BindKey(EKeys::Q, IE_Pressed, this, &ARLViewerPawn::ZoomOut);

	// Number keys follow a player with ball cam.
	const FKey NumberKeys[] = {EKeys::One, EKeys::Two, EKeys::Three, EKeys::Four, EKeys::Five, EKeys::Six, EKeys::Seven, EKeys::Eight};
	for (int32 I = 0; I < UE_ARRAY_COUNT(NumberKeys); ++I)
	{
		FInputKeyBinding Binding(FInputChord(NumberKeys[I]), IE_Pressed);
		Binding.KeyDelegate.GetDelegateForManualSet().BindLambda([this, I]() { FocusPlayer(I); });
		Input->KeyBindings.Add(Binding);
	}

	// Orbit: hold the right mouse button and drag, or use A/D and W/S.
	Input->BindAxisKey(EKeys::MouseX, this, &ARLViewerPawn::MouseX);
	Input->BindAxisKey(EKeys::MouseY, this, &ARLViewerPawn::MouseY);
}

void ARLViewerPawn::TogglePlay()
{
	if (Director.IsValid())
	{
		Director->GetClock().TogglePlay();
	}
}

void ARLViewerPawn::StepBack()
{
	if (Director.IsValid())
	{
		Director->GetClock().Step(-5.0);
	}
}

void ARLViewerPawn::StepForward()
{
	if (Director.IsValid())
	{
		Director->GetClock().Step(5.0);
	}
}

void ARLViewerPawn::Faster()
{
	if (Director.IsValid())
	{
		Director->GetClock().Faster();
	}
}

void ARLViewerPawn::Slower()
{
	if (Director.IsValid())
	{
		Director->GetClock().Slower();
	}
}

void ARLViewerPawn::NextMoment()
{
	if (Director.IsValid())
	{
		Director->JumpToNextMoment();
	}
}

void ARLViewerPawn::PreviousMoment()
{
	if (Director.IsValid())
	{
		Director->JumpToPreviousMoment();
	}
}

void ARLViewerPawn::FocusPlayer(int32 Index)
{
	if (Director.IsValid() && Director->GetReplay().Players.IsValidIndex(Index))
	{
		Director->SetFocusPlayer(Index);
		SetCameraMode(ERLCameraMode::BallCam);
	}
}

void ARLViewerPawn::ClearFocus()
{
	if (Director.IsValid())
	{
		Director->SetFocusPlayer(INDEX_NONE);
	}
	SetCameraMode(ERLCameraMode::Orbit);
}

void ARLViewerPawn::ZoomIn()
{
	OrbitDistance = FMath::Clamp(OrbitDistance * 0.85, MinOrbitDistance, MaxOrbitDistance);
}

void ARLViewerPawn::ZoomOut()
{
	OrbitDistance = FMath::Clamp(OrbitDistance / 0.85, MinOrbitDistance, MaxOrbitDistance);
}

// A/D and W/S orbit at a steady rate while held. They are read here rather
// than bound as axes: keyboard keys aren't 1D axes, and binding them as one
// trips an engine check.
void ARLViewerPawn::PollOrbitKeys(float DeltaSeconds)
{
	const APlayerController* PC = Cast<APlayerController>(GetController());
	if (!PC)
	{
		return;
	}
	auto Held = [PC](const FKey& Key) { return PC->IsInputKeyDown(Key) ? 1.0 : 0.0; };
	OrbitYawDeg += (Held(EKeys::D) - Held(EKeys::A)) * 90.0 * DeltaSeconds;
	OrbitPitchDeg = FMath::Clamp(OrbitPitchDeg + (Held(EKeys::W) - Held(EKeys::S)) * 45.0 * DeltaSeconds, 5.0, 89.0);
}

void ARLViewerPawn::SetOrbit(double YawDeg, double PitchDeg, double Distance)
{
	OrbitYawDeg = YawDeg;
	OrbitPitchDeg = FMath::Clamp(PitchDeg, 5.0, 89.0);
	OrbitDistance = FMath::Clamp(Distance, MinOrbitDistance, MaxOrbitDistance);
}

void ARLViewerPawn::MouseX(float Value)
{
	const APlayerController* PC = Cast<APlayerController>(GetController());
	if (PC && PC->IsInputKeyDown(EKeys::RightMouseButton))
	{
		OrbitYawDeg += Value * 2.0;
	}
}

void ARLViewerPawn::MouseY(float Value)
{
	const APlayerController* PC = Cast<APlayerController>(GetController());
	if (PC && PC->IsInputKeyDown(EKeys::RightMouseButton))
	{
		OrbitPitchDeg = FMath::Clamp(OrbitPitchDeg - Value * 2.0, 5.0, 89.0);
	}
}
