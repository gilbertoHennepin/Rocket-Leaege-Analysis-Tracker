#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerController.h"

#include "RLViewerPlayerController.generated.h"

// Shows the mouse cursor so the on-screen controls can be clicked, while
// keyboard shortcuts and right-drag orbiting keep working.
UCLASS()
class RLREPLAYVIEWER_API ARLViewerPlayerController : public APlayerController
{
	GENERATED_BODY()

public:
	ARLViewerPlayerController();

protected:
	virtual void BeginPlay() override;
};
