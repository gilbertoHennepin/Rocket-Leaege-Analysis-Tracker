#include "RLArena.h"

#include "Components/SceneComponent.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "RLShapes.h"

namespace
{
// Boost pad locations on a standard Soccar field (x, y).
const FVector2D BigPads[] = {{-3072, -4096}, {3072, -4096}, {-3584, 0}, {3584, 0}, {-3072, 4096}, {3072, 4096}};
const FVector2D SmallPads[] = {
	{0, -4240}, {-1792, -4184}, {1792, -4184}, {-940, -3308}, {940, -3308}, {0, -2816}, {-3584, -2484},
	{3584, -2484}, {-1788, -2300}, {1788, -2300}, {-2048, -1036}, {0, -1024}, {2048, -1036}, {-1024, 0},
	{1024, 0}, {-2048, 1036}, {0, 1024}, {2048, 1036}, {-1788, 2300}, {1788, 2300}, {-3584, 2484},
	{3584, 2484}, {0, 2816}, {-940, 3310}, {940, 3308}, {-1792, 4184}, {1792, 4184}, {0, 4240}};
}  // namespace

ARLArena::ARLArena()
{
	PrimaryActorTick.bCanEverTick = false;
	RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

void ARLArena::BeginPlay()
{
	Super::BeginPlay();
	BuildFloor();
	BuildWalls();
	BuildGoal(0);
	BuildGoal(1);
	BuildBoostPads();
}

void ARLArena::BuildFloor()
{
	using namespace RLShapes;
	UMaterialInstanceDynamic* Field = MakeMaterial(this, Colors::Field);
	UMaterialInstanceDynamic* Lines = MakeMaterial(this, Colors::Lines);

	// The floor is slightly larger than the field so the corner cuts and goal
	// mouths sit on it; walls mark the real boundary.
	AddShape(this, Plane(), FVector::ZeroVector, FVector(HalfWidth * 2, (HalfLength + GoalDepth) * 2, 1), Field);

	// Halfway line and center circle (thin boxes just above the floor).
	AddShape(this, Cube(), FVector(0, 0, 1), FVector(HalfWidth * 2, 16, 2), Lines);
	constexpr int32 Segments = 48;
	constexpr double Radius = 1000.0;
	const double SegmentLength = 2 * PI * Radius / Segments + 4;
	for (int32 I = 0; I < Segments; ++I)
	{
		const double Angle = 2 * PI * I / Segments;
		const FVector Location(Radius * FMath::Cos(Angle), Radius * FMath::Sin(Angle), 1);
		const FRotator Rotation(0, FMath::RadiansToDegrees(Angle) + 90, 0);
		AddShape(this, Cube(), Location, FVector(SegmentLength, 16, 2), Lines, Rotation);
	}
}

void ARLArena::AddWall(const FVector2D& From, const FVector2D& To, double Thickness, double Height, UMaterialInstanceDynamic* Material)
{
	const FVector2D Mid = (From + To) / 2;
	const FVector2D Delta = To - From;
	const FRotator Rotation(0, FMath::RadiansToDegrees(FMath::Atan2(Delta.Y, Delta.X)), 0);
	RLShapes::AddShape(this, RLShapes::Cube(), FVector(Mid.X, Mid.Y, Height / 2), FVector(Delta.Size(), Thickness, Height), Material, Rotation);
}

void ARLArena::BuildWalls()
{
	UMaterialInstanceDynamic* Walls = RLShapes::MakeMaterial(this, RLShapes::Colors::Walls);
	const double W = HalfWidth;
	const double L = HalfLength;
	const double C = CornerCut;
	const double G = GoalHalfWidth;
	constexpr double T = 40.0;

	// Walk the outline: side walls, corner cuts, and back walls either side of each goal.
	const FVector2D Outline[] = {
		{-W, -L + C}, {-W, L - C}, {-W + C, L}, {-G, L}, {G, L}, {W - C, L}, {W, L - C}, {W, -L + C},
		{W - C, -L}, {G, -L}, {-G, -L}, {-W + C, -L}};
	constexpr int32 Num = UE_ARRAY_COUNT(Outline);
	for (int32 I = 0; I < Num; ++I)
	{
		const FVector2D& A = Outline[I];
		const FVector2D& B = Outline[(I + 1) % Num];
		const bool bGoalMouth = FMath::IsNearlyEqual(FMath::Abs(A.X), G) && FMath::IsNearlyEqual(FMath::Abs(B.X), G);
		if (!bGoalMouth)
		{
			AddWall(A, B, T, WallHeight, Walls);
		}
	}
}

void ARLArena::BuildGoal(int32 Team)
{
	using namespace RLShapes;
	UMaterialInstanceDynamic* Frame = MakeMaterial(this, Colors::Team(Team));
	// Blue defends negative y.
	const double Sign = Team == 0 ? -1.0 : 1.0;
	const double Line = Sign * HalfLength;
	const double Back = Sign * (HalfLength + GoalDepth);
	constexpr double Post = 40.0;

	// Posts and crossbar on the goal line.
	for (const double X : {-GoalHalfWidth, GoalHalfWidth})
	{
		AddShape(this, Cylinder(), FVector(X, Line, GoalHeight / 2), FVector(Post, Post, GoalHeight), Frame);
	}
	AddShape(this, Cylinder(), FVector(0, Line, GoalHeight), FVector(Post, Post, GoalHalfWidth * 2), Frame, FRotator(90, 0, 0));

	// The net box: side and back walls as low rails, plus a top frame.
	UMaterialInstanceDynamic* Net = MakeMaterial(this, Colors::Team(Team) * 0.45f);
	AddWall({-GoalHalfWidth, Line}, {-GoalHalfWidth, Back}, 20, GoalHeight, Net);
	AddWall({GoalHalfWidth, Line}, {GoalHalfWidth, Back}, 20, GoalHeight, Net);
	AddWall({-GoalHalfWidth, Back}, {GoalHalfWidth, Back}, 20, GoalHeight, Net);

	// Tint the floor inside the goal so it reads as the team's end.
	AddShape(this, Cube(), FVector(0, (Line + Back) / 2, 1), FVector(GoalHalfWidth * 2, GoalDepth, 2), Net);
}

void ARLArena::BuildBoostPads()
{
	using namespace RLShapes;
	UMaterialInstanceDynamic* Boost = MakeMaterial(this, Colors::Boost);
	for (const FVector2D& P : BigPads)
	{
		AddShape(this, Cylinder(), FVector(P.X, P.Y, 6), FVector(200, 200, 12), Boost);
	}
	for (const FVector2D& P : SmallPads)
	{
		AddShape(this, Cylinder(), FVector(P.X, P.Y, 4), FVector(90, 90, 8), Boost);
	}
}
