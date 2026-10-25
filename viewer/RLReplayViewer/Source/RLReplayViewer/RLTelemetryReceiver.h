// The network side of live mode: a UDP socket read on a background thread.

#pragma once

#include "CoreMinimal.h"
#include "Containers/Queue.h"

class FSocket;
class FUdpSocketReceiver;

// Receives telemetry datagrams on a background thread (Unreal's
// FUdpSocketReceiver) and stamps each with its arrival time there, so jitter
// is measured to the millisecond rather than to the game's frame rate. The
// game thread drains them once per frame. See docs/telemetry.md.
class RLREPLAYVIEWER_API FRLTelemetryReceiver
{
public:
	struct FDatagram
	{
		double ArrivalSeconds = 0.0;  // FPlatformTime::Seconds() when it arrived
		TArray<uint8> Bytes;
	};

	// Both defined in the .cpp, where FUdpSocketReceiver is a complete type.
	FRLTelemetryReceiver();
	~FRLTelemetryReceiver();

	// Listen on Host:Port: 127.0.0.1 for this computer only, 0.0.0.0 for any.
	bool Start(const FString& Host, int32 Port, FString& OutError);
	void Stop();
	bool IsRunning() const { return Socket != nullptr; }
	const FString& GetAddress() const { return Address; }

	// Game thread: the next datagram that has arrived, if any.
	bool Dequeue(FDatagram& Out) { return Queue.Dequeue(Out); }

private:
	FSocket* Socket = nullptr;
	TUniquePtr<FUdpSocketReceiver> Thread;
	TQueue<FDatagram, EQueueMode::Spsc> Queue;  // one producer (the thread), one consumer (the game)
	FString Address;
};
