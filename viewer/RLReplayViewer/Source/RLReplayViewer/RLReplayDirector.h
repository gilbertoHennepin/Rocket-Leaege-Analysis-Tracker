#pragma once

#include "CoreMinimal.h"
#include "Core/ReplayCore.h"
#include "GameFramework/Actor.h"
#include "RLReplayData.h"

#include "RLReplayDirector.generated.h"

class UMaterialInstanceDynamic;
class UStaticMeshComponent;
class UTextRenderComponent;

// Owns the loaded match and the playback clock, and moves the ball and car
// visuals to match the current replay time every frame.
UCLASS()
class RLREPLAYVIEWER_API ARLReplayDirector : public AActor
{
	GENERATED_BODY()

public:
	ARLReplayDirector();

	bool LoadReplay(const FString& Path, FString& OutError);
	bool HasReplay() const { return !Replay.IsEmpty(); }

	const FRLReplay& GetReplay() const { return Replay; }
	rlcore::PlaybackClock& GetClock() { return Clock; }
	const rlcore::PlaybackClock& GetClock() const { return Clock; }
	const FString& GetLoadedPath() const { return LoadedPath; }

	// Positions at the current time, in world space.
	FVector GetBallLocation() const;
	FVector GetCarLocation(int32 PlayerIndex) const;
	double GetCarBoost(int32 PlayerIndex) const;
	FString GetClockText() const;

	// The player the camera and moment list focus on, or INDEX_NONE.
	int32 GetFocusPlayer() const { return FocusPlayer; }
	void SetFocusPlayer(int32 PlayerIndex);

	// Jump a little before a coaching moment so its lead-up is visible.
	void JumpToMoment(int32 MomentIndex);
	void JumpToNextMoment();
	void JumpToPreviousMoment();
	int32 GetActiveMoment() const { return ActiveMoment; }

	static constexpr double MomentLeadIn = 2.5;  // seconds shown before a moment

	virtual void Tick(float DeltaSeconds) override;

private:
	struct FCarVisual
	{
		USceneComponent* Root = nullptr;
		UStaticMeshComponent* Body = nullptr;
		UTextRenderComponent* Label = nullptr;
		UTextRenderComponent* MomentLabel = nullptr;
		int32 ShownBoost = -1;
	};

	void ClearVisuals();
	void BuildVisuals();
	void UpdateVisuals();
	void UpdateLabels(const FVector& CameraLocation);
	void DrawBallTrail() const;
	int32 FindMomentAt(double Time, int32 PlayerIndex) const;

	FRLReplay Replay;
	std::vector<double> MomentTimes;
	rlcore::PlaybackClock Clock;
	rlcore::SamplePoint Sample;
	FString LoadedPath;
	int32 FocusPlayer = INDEX_NONE;
	int32 ActiveMoment = INDEX_NONE;

	UPROPERTY()
	TObjectPtr<UStaticMeshComponent> BallMesh;

	UPROPERTY()
	TArray<TObjectPtr<USceneComponent>> SpawnedComponents;

	TArray<FCarVisual> Cars;
};
