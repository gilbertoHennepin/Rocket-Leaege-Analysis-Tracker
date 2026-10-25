#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Pawn.h"

#include "RLViewerPawn.generated.h"

class ARLReplayDirector;
class UCameraComponent;

UENUM()
enum class ERLCameraMode : uint8
{
	Orbit,    // orbit the ball (mouse / keyboard to rotate and zoom)
	BallCam,  // behind the focused player, looking at the ball
	TopDown,  // whole field from above, like the heat maps
};

// Spectator camera. All keyboard and mouse controls live here.
UCLASS()
class RLREPLAYVIEWER_API ARLViewerPawn : public APawn
{
	GENERATED_BODY()

public:
	ARLViewerPawn();

	void SetDirector(ARLReplayDirector* InDirector) { Director = InDirector; }
	ERLCameraMode GetCameraMode() const { return Mode; }
	void SetCameraMode(ERLCameraMode NewMode);
	void CycleCameraMode();
	FText GetCameraModeText() const;

	// Follow a player in ball cam, or go back to the free orbit camera.
	void FocusPlayer(int32 Index);
	void ClearFocus();

	// Place the orbit camera (degrees, and distance from the ball in uu).
	void SetOrbit(double YawDeg, double PitchDeg, double Distance);

	// Toggled with H; the HUD reads this.
	bool IsHudVisible() const { return bHudVisible; }

	virtual void Tick(float DeltaSeconds) override;
	virtual void SetupPlayerInputComponent(UInputComponent* PlayerInputComponent) override;

private:
	void TogglePlay();
	void StepBack();
	void StepForward();
	void Faster();
	void Slower();
	void NextMoment();
	void PreviousMoment();
	void ToggleHud() { bHudVisible = !bHudVisible; }
	void ZoomIn();
	void ZoomOut();
	void PollOrbitKeys(float DeltaSeconds);
	void MouseX(float Value);
	void MouseY(float Value);

	UPROPERTY(VisibleAnywhere)
	TObjectPtr<UCameraComponent> Camera;

	TWeakObjectPtr<ARLReplayDirector> Director;
	ERLCameraMode Mode = ERLCameraMode::Orbit;
	bool bHudVisible = true;

	double OrbitYawDeg = -90.0;  // start behind the blue goal
	double OrbitPitchDeg = 34.0;
	double OrbitDistance = 4200.0;

	// Smoothed camera target, so cuts between moments don't jolt the view.
	FVector SmoothedLocation = FVector::ZeroVector;
	FVector SmoothedLookAt = FVector::ZeroVector;
	bool bHasSmoothed = false;
};
