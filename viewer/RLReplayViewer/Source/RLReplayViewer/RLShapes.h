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

// Lighter versions for text on the HUD's dark panels, chosen for at least
// 4.5:1 contrast (WCAG AA) on those panels even over a white background.
const FLinearColor BlueText = FLinearColor::FromSRGBColor(FColor(0x8C, 0xC2, 0xFF));
const FLinearColor OrangeText = FLinearColor::FromSRGBColor(FColor(0xFF, 0xA8, 0x73));

inline FLinearColor TeamText(int32 TeamIndex)
{
	return TeamIndex == 0 ? BlueText : OrangeText;
}

// Coaching moment severity, for text: "high" red, "medium" amber, anything else grey.
inline FLinearColor SeverityText(const FString& Severity)
{
	if (Severity == TEXT("high"))
	{
		return FLinearColor::FromSRGBColor(FColor(0xFF, 0x9A, 0x90));
	}
	if (Severity == TEXT("medium"))
	{
		return FLinearColor::FromSRGBColor(FColor(0xFF, 0xC2, 0x33));
	}
	return FLinearColor::FromSRGBColor(FColor(0xCF, 0xCD, 0xC4));
}
}  // namespace Colors
}  // namespace RLShapes
