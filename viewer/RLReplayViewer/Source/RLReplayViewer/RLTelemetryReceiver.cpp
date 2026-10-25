#include "RLTelemetryReceiver.h"

#include "Common/UdpSocketBuilder.h"
#include "Common/UdpSocketReceiver.h"
#include "HAL/PlatformTime.h"
#include "Interfaces/IPv4/IPv4Address.h"
#include "Interfaces/IPv4/IPv4Endpoint.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

FRLTelemetryReceiver::FRLTelemetryReceiver() = default;

FRLTelemetryReceiver::~FRLTelemetryReceiver()
{
	Stop();
}

bool FRLTelemetryReceiver::Start(const FString& Host, int32 Port, FString& OutError)
{
	Stop();
	FIPv4Address Ip;
	if (!FIPv4Address::Parse(Host, Ip))
	{
		OutError = FString::Printf(TEXT("%s isn't an IPv4 address"), *Host);
		return false;
	}
	if (Port < 1 || Port > 65535)
	{
		OutError = FString::Printf(TEXT("%d isn't a port number"), Port);
		return false;
	}

	const FIPv4Endpoint Endpoint(Ip, static_cast<uint16>(Port));
	Socket = FUdpSocketBuilder(TEXT("RLTelemetry"))
				 .AsNonBlocking()
				 .BoundToEndpoint(Endpoint)
				 .WithReceiveBufferSize(1 << 20)
				 .Build();
	if (!Socket)
	{
		OutError = FString::Printf(TEXT("couldn't listen on %s (is something else using that port?)"), *Endpoint.ToString());
		return false;
	}
	Address = Endpoint.ToString();

	// Runs on the receiver thread: stamp the arrival time and hand the bytes over.
	Thread = MakeUnique<FUdpSocketReceiver>(Socket, FTimespan::FromMilliseconds(100), TEXT("RLTelemetryReceiver"));
	Thread->OnDataReceived().BindLambda([this](const FArrayReaderPtr& Data, const FIPv4Endpoint&) {
		Queue.Enqueue(FDatagram{FPlatformTime::Seconds(), TArray<uint8>(Data->GetData(), Data->Num())});
	});
	Thread->Start();
	return true;
}

void FRLTelemetryReceiver::Stop()
{
	// Stop the thread (it joins in its destructor) before closing its socket.
	Thread.Reset();
	if (Socket)
	{
		Socket->Close();
		ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM)->DestroySocket(Socket);
		Socket = nullptr;
	}
	FDatagram Discard;
	while (Queue.Dequeue(Discard))
	{
	}
	Address.Reset();
}
