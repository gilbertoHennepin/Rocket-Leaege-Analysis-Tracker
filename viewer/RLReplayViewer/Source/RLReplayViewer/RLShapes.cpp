#include "RLShapes.h"

#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "GameFramework/Actor.h"
#include "Materials/Material.h"
#include "Materials/MaterialInstanceDynamic.h"

namespace
{
UStaticMesh* LoadMesh(const TCHAR* Path)
{
	UStaticMesh* Mesh = LoadObject<UStaticMesh>(nullptr, Path);
	ensureMsgf(Mesh, TEXT("Missing engine mesh %s"), Path);
	return Mesh;
}
}  // namespace

namespace RLShapes
{
UStaticMesh* Cube()
{
	return LoadMesh(TEXT("/Engine/BasicShapes/Cube.Cube"));
}

UStaticMesh* Sphere()
{
	return LoadMesh(TEXT("/Engine/BasicShapes/Sphere.Sphere"));
}

UStaticMesh* Cylinder()
{
	return LoadMesh(TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
}

UStaticMesh* Cone()
{
	return LoadMesh(TEXT("/Engine/BasicShapes/Cone.Cone"));
}

UStaticMesh* Plane()
{
	return LoadMesh(TEXT("/Engine/BasicShapes/Plane.Plane"));
}

UMaterialInstanceDynamic* MakeMaterial(UObject* Outer, const FLinearColor& Color)
{
	UMaterialInterface* Base = LoadObject<UMaterialInterface>(nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
	if (!Base)
	{
		Base = UMaterial::GetDefaultMaterial(MD_Surface);
	}
	UMaterialInstanceDynamic* Material = UMaterialInstanceDynamic::Create(Base, Outer);
	Material->SetVectorParameterValue(TEXT("Color"), Color);
	return Material;
}

UStaticMeshComponent* AddShape(AActor* Owner, UStaticMesh* Mesh, const FVector& Location, const FVector& SizeUU,
	UMaterialInstanceDynamic* Material, const FRotator& Rotation, USceneComponent* Parent)
{
	check(Owner);
	UStaticMeshComponent* Component = NewObject<UStaticMeshComponent>(Owner);
	Component->SetStaticMesh(Mesh);
	Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
	Component->SetGenerateOverlapEvents(false);
	Component->SetMobility(EComponentMobility::Movable);
	Component->SetupAttachment(Parent ? Parent : Owner->GetRootComponent());
	Component->SetRelativeLocationAndRotation(Location, Rotation);
	Component->SetRelativeScale3D(SizeUU / ShapeSize);
	if (Material)
	{
		Component->SetMaterial(0, Material);
	}
	Component->RegisterComponent();
	Owner->AddInstanceComponent(Component);
	return Component;
}
}  // namespace RLShapes
