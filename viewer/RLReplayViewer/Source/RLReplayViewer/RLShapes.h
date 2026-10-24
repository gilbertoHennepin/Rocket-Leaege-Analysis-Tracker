// Helpers to build the scene from the engine's basic shapes, so the project
// needs no mesh or material assets of its own.

#pragma once

#include "CoreMinimal.h"

class AActor;
class UMaterialInstanceDynamic;
class UStaticMesh;
class UStaticMeshComponent;
class USceneComponent;

namespace RLShapes
{
// Engine basic shapes are 100 uu across, centered on their origin.
constexpr double ShapeSize = 100.0;

UStaticMesh* Cube();
UStaticMesh* Sphere();
UStaticMesh* Cylinder();
UStaticMesh* Cone();
UStaticMesh* Plane();

// A flat-colored material based on the engine's basic shape material.
UMaterialInstanceDynamic* MakeMaterial(UObject* Outer, const FLinearColor& Color);

// Add a mesh component to Owner (attached to Parent, or the root), sized in
// Unreal units rather than as a scale of the 100 uu shape.
UStaticMeshComponent* AddShape(AActor* Owner, UStaticMesh* Mesh, const FVector& Location, const FVector& SizeUU,
	UMaterialInstanceDynamic* Material, const FRotator& Rotation = FRotator::ZeroRotator,
	USceneComponent* Parent = nullptr);

// Colors shared with the Python heat maps, so the two look like one product.
namespace Colors
{
const FLinearColor Blue = FLinearColor::FromSRGBColor(FColor(0x39, 0x87, 0xE5));
const FLinearColor Orange = FLinearColor::FromSRGBColor(FColor(0xD9, 0x59, 0x26));
const FLinearColor Ball = FLinearColor::FromSRGBColor(FColor(0xE8, 0xE6, 0xE0));
const FLinearColor Field = FLinearColor::FromSRGBColor(FColor(0x22, 0x2B, 0x22));
const FLinearColor Lines = FLinearColor::FromSRGBColor(FColor(0x8A, 0x92, 0x88));
const FLinearColor Walls = FLinearColor::FromSRGBColor(FColor(0x4A, 0x49, 0x45));
const FLinearColor Boost = FLinearColor::FromSRGBColor(FColor(0xC9, 0x85, 0x00));

inline FLinearColor Team(int32 TeamIndex)
{
	return TeamIndex == 0 ? Blue : Orange;
}

// Coaching moment severity: "high" red, "medium" amber, anything else grey.
inline FColor SeverityColor(const FString& Severity)
{
	if (Severity == TEXT("high"))
	{
		return FColor(0xE3, 0x49, 0x48);
	}
	if (Severity == TEXT("medium"))
	{
		return FColor(0xED, 0xA1, 0x00);
	}
	return FColor(0xC3, 0xC2, 0xB7);
}
}  // namespace Colors
}  // namespace RLShapes
