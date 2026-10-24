#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "RLArena.generated.h"

// A standard Rocket League field (Soccar) built from basic shapes: floor,
// markings, low walls, goals and boost pads. Dimensions match the replay
// coordinates exactly, so replay positions need no conversion.
UCLASS()
class RLREPLAYVIEWER_API ARLArena : public AActor
{
	GENERATED_BODY()

public:
	ARLArena();

	// Walls are drawn lower than the real 2044 uu so the camera can see in.
	UPROPERTY(EditAnywhere, Category = "Arena")
	double WallHeight = 260.0;

	static constexpr double HalfWidth = 4096.0;   // x
	static constexpr double HalfLength = 5120.0;  // y
	static constexpr double CornerCut = 1152.0;
	static constexpr double GoalHalfWidth = 892.755;
	static constexpr double GoalHeight = 642.775;
	static constexpr double GoalDepth = 880.0;

protected:
	virtual void BeginPlay() override;

private:
	void BuildFloor();
	void BuildWalls();
	void BuildGoal(int32 Team);
	void BuildBoostPads();
	void AddWall(const FVector2D& From, const FVector2D& To, double Thickness, double Height, class UMaterialInstanceDynamic* Material);
};
