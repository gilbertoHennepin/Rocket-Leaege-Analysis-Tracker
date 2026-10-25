// Unit tests for the engine-free live telemetry core. Plain C++, no engine.

#include "../RLReplayViewer/Source/RLReplayViewer/Core/TelemetryCore.h"
#include "check.h"

#include <cstdint>
#include <cstring>
#include <limits>
#include <string>
#include <vector>

using namespace rlcore;
using namespace rlcore::telemetry;

// The packet tests/test_telemetry.py builds with the Python sender, byte for
// byte: stream 0x1234, sequence 42, sent at 1.5 s, match time 12.5 s, the
// ball and two cars facing each other.
static const char* GoldenFrame =
	"524c5431010234122a000000dc050000"
	"0000484100a08f4301020000c8420000"
	"48c30080b942000000000000ff7f0000"
	"0000000090c50000884100000000825a"
	"825a2100000000000090450000884100"
	"0000007ea5825a64";

static std::vector<std::uint8_t> FromHex(const char* Hex)
{
	std::vector<std::uint8_t> Bytes;
	for (std::size_t I = 0; Hex[I] != '\0' && Hex[I + 1] != '\0'; I += 2)
	{
		Bytes.push_back(static_cast<std::uint8_t>(std::stoi(std::string(Hex + I, 2), nullptr, 16)));
	}
	return Bytes;
}

// Builds packets the way the Python sender does.
class PacketWriter
{
public:
	PacketWriter(PacketType Type, std::uint16_t Stream, std::uint32_t Sequence, std::uint32_t SentMs)
	{
		Text("RLT1").U8(ProtocolVersion).U8(static_cast<unsigned>(Type)).U16(Stream).U32(Sequence).U32(SentMs);
	}

	PacketWriter& U8(unsigned Value)
	{
		Bytes.push_back(static_cast<std::uint8_t>(Value & 0xFF));
		return *this;
	}
	PacketWriter& U16(unsigned Value) { return U8(Value).U8(Value >> 8); }
	PacketWriter& U32(std::uint32_t Value) { return U16(Value & 0xFFFF).U16(Value >> 16); }
	PacketWriter& F32(float Value)
	{
		std::uint32_t Bits = 0;
		std::memcpy(&Bits, &Value, sizeof Bits);
		return U32(Bits);
	}
	PacketWriter& Text(const std::string& Value)
	{
		Bytes.insert(Bytes.end(), Value.begin(), Value.end());
		return *this;
	}
	// A body at a position with no rotation.
	PacketWriter& Unrotated(float X, float Y, float Z) { return F32(X).F32(Y).F32(Z).U16(0).U16(0).U16(0).U16(32767); }

	std::vector<std::uint8_t> Bytes;
};

// A one-car frame.
static std::vector<std::uint8_t> FramePacket(std::uint16_t Stream, std::uint32_t Sequence, std::uint32_t SentMs, float Time)
{
	PacketWriter W(PacketType::Frame, Stream, Sequence, SentMs);
	W.F32(Time).F32(300.0f - Time).U8(1).U8(1);
	W.Unrotated(0.0f, 0.0f, 93.0f).Unrotated(0.0f, -4608.0f, 17.0f).U8(33);
	return W.Bytes;
}

static std::vector<std::uint8_t> HelloPacket(std::uint16_t Stream, std::uint32_t Sequence, std::uint32_t SentMs)
{
	return PacketWriter(PacketType::Hello, Stream, Sequence, SentMs).Text(R"({"mode":"1v1"})").Bytes;
}

static std::vector<std::uint8_t> EventPacket(std::uint16_t Stream, std::uint32_t Sequence, std::uint32_t Id)
{
	return PacketWriter(PacketType::Event, Stream, Sequence, Sequence * 10).U32(Id).Text(R"({"event":"goal"})").Bytes;
}

static bool Parses(const std::vector<std::uint8_t>& Bytes, Packet& Out)
{
	std::string Error;
	return ParsePacket(Bytes.data(), Bytes.size(), Out, Error);
}

static void TestGoldenFrame()
{
	const std::vector<std::uint8_t> Bytes = FromHex(GoldenFrame);
	CHECK(Bytes.size() == 88);  // header 16 + frame head 10 + ball 20 + two cars of 21

	Packet P;
	CHECK(Parses(Bytes, P));
	CHECK(P.Head.Type == PacketType::Frame);
	CHECK(P.Head.Stream == 0x1234 && P.Head.Sequence == 42 && P.Head.SentMs == 1500);

	const Frame& F = P.FrameData;
	CHECK(F.Time == 12.5 && F.Clock == 287.25 && F.bLive);
	CHECK(Near(F.Ball.Pos, {100, -200, 92.75}));
	CHECK(Near(F.Ball.Rot.W, 1.0));
	CHECK(F.Cars.size() == 2);
	CHECK((F.Boost == std::vector<int>{33, 100}));
	CHECK(Near(F.Cars[0].Pos, {0, -4608, 17}));
	CHECK(Near(F.Cars[1].Pos, {0, 4608, 17}));
	// Yaw +90 and -90 degrees: the blue car faces +Y, the orange car -Y.
	CHECK(Near(F.Cars[0].Rot.Rotate({1, 0, 0}), {0, 1, 0}, 1e-4));
	CHECK(Near(F.Cars[1].Rot.Rotate({1, 0, 0}), {0, -1, 0}, 1e-4));
}

static void TestJsonPackets()
{
	Packet P;
	CHECK(Parses(HelloPacket(7, 0, 0), P));
	CHECK(P.Head.Type == PacketType::Hello && P.Head.Stream == 7 && P.Json == R"({"mode":"1v1"})");

	CHECK(Parses(EventPacket(7, 5, 3), P));
	CHECK(P.Head.Type == PacketType::Event && P.EventId == 3 && P.Json == R"({"event":"goal"})");

	CHECK(Parses(PacketWriter(PacketType::Bye, 7, 9, 900).Bytes, P));
	CHECK(P.Head.Type == PacketType::Bye && P.Head.Sequence == 9 && P.Head.SentMs == 900 && P.Json.empty());
}

static void TestRejectsBadPackets()
{
	const std::vector<std::uint8_t> Good = FromHex(GoldenFrame);
	auto Rejects = [](std::vector<std::uint8_t> Bytes) {
		Packet P;
		std::string Error;
		const bool bParsed = ParsePacket(Bytes.data(), Bytes.size(), P, Error);
		return !bParsed && !Error.empty();
	};
	auto With = [&Good](std::size_t Offset, std::uint8_t Value) {
		std::vector<std::uint8_t> Bytes = Good;
		Bytes[Offset] = Value;
		return Bytes;
	};

	CHECK(Rejects({}));
	CHECK(Rejects(std::vector<std::uint8_t>(Good.begin(), Good.begin() + 15)));  // shorter than the header
	CHECK(Rejects(With(0, 'X')));  // wrong magic
	CHECK(Rejects(With(4, 2)));    // unknown version
	CHECK(Rejects(With(5, 9)));    // unknown type
	CHECK(Rejects(With(5, 0)));
	CHECK(Rejects(std::vector<std::uint8_t>(Good.begin(), Good.end() - 1)));  // truncated car
	CHECK(Rejects(With(25, 3)));  // claims a third car
	CHECK(Rejects(With(25, 255)));
	CHECK(Rejects(PacketWriter(PacketType::Event, 1, 1, 1).U8(1).Bytes));  // event without an id
	CHECK(Rejects(PacketWriter(PacketType::Frame, 1, 1, 1).U8(0).Bytes));  // truncated frame head

	// A NaN position would poison the camera and every interpolation after it.
	std::vector<std::uint8_t> NotANumber = FramePacket(1, 1, 1, 1.0f);
	const float Nan = std::numeric_limits<float>::quiet_NaN();
	std::memcpy(NotANumber.data() + 16 + 10, &Nan, sizeof Nan);
	CHECK(Rejects(NotANumber));

	Packet P;
	std::string Error;
	CHECK(!ParsePacket(nullptr, 0, P, Error));
}

static void TestLinkStats()
{
	LinkStats Stats;
	for (std::uint32_t Sequence : {0u, 1u, 2u, 4u, 3u, 3u, 7u, 8u})  // 5 and 6 lost, 3 late and doubled
	{
		Stats.Add(Sequence, Sequence * 100, Sequence * 0.1);
	}
	CHECK(Stats.Received == 7 && Stats.Reordered == 1 && Stats.Duplicates == 1);
	CHECK(Stats.Expected() == 9 && Stats.Lost() == 2);
	CHECK(Near(Stats.LossPercent(), 200.0 / 9.0));

	// Reordering before the first packet to arrive isn't loss.
	LinkStats Early;
	for (std::uint32_t Sequence : {5u, 3u, 4u})
	{
		Early.Add(Sequence, 0, 0.0);
	}
	CHECK(Early.Expected() == 3 && Early.Lost() == 0 && Early.Reordered == 2);

	LinkStats Window;
	for (std::uint32_t Sequence = 0; Sequence < 3000; ++Sequence)
	{
		Window.Add(Sequence, Sequence, Sequence / 1000.0);
	}
	CHECK(!Window.Add(2999 - DuplicateWindow, 0, 3.0));      // too old to tell: ignored as late
	CHECK(!Window.Add(2999 - DuplicateWindow + 1, 0, 3.0));  // still remembered: a duplicate
	CHECK(Window.Late == 1 && Window.Duplicates == 1 && Window.Lost() == 0);

	LinkStats Empty;
	CHECK(Empty.Expected() == 0 && Empty.LossPercent() == 0.0);
}

static void TestJitter()
{
	LinkStats Steady;
	for (std::uint32_t I = 0; I < 50; ++I)
	{
		Steady.Add(I, I * 100, I * 0.1 + 0.020);  // always 20 ms in flight
	}
	CHECK(Near(Steady.JitterMs, 0.0, 1e-6));

	LinkStats Bumpy;
	for (std::uint32_t I = 0; I < 200; ++I)
	{
		Bumpy.Add(I, I * 100, I * 0.1 + (I % 2 == 1 ? 0.010 : 0.0));  // alternately 0 and 10 ms late
	}
	CHECK(Near(Bumpy.JitterMs, 10.0, 0.01));  // converges to the mean difference
}

static void TestPlayoutBuffer()
{
	PlayoutBuffer Playout;
	CHECK(!Playout.IsReady());

	// Frames every 100 ms, each 50 ms in flight. The clocks are 1000 s apart,
	// which must not matter: only differences of sender times are used.
	for (std::uint32_t I = 0; I < 30; ++I)
	{
		Playout.AddPacket(I * 100, 1000.0 + I * 0.1 + 0.050);
		Playout.AddFrame(I * 100);
	}
	CHECK(Playout.IsReady());
	CHECK(Near(Playout.GetFrameInterval(), 0.1));

	// Right after the newest frame (sent at 2.9 s) arrives, a clean link shows
	// the sender's 2.8 s: one frame back, so there is a frame to move towards.
	const double Now = 1000.0 + 2.9 + 0.050;
	CHECK(Near(Playout.SenderTimeAt(Now, 0.0), 2.8));
	// Jitter adds headroom: 10 ms of jitter buffers 40 ms more.
	CHECK(Near(Playout.Depth(10.0), 0.14));
	CHECK(Near(Playout.SenderTimeAt(Now, 10.0), 2.76));

	// If the path gets slower for good, the windowed minimum catches up
	// within two windows, rather than leaving playback starved.
	PlayoutBuffer Slower;
	for (std::uint32_t I = 0; I < 400; ++I)
	{
		const double Transit = I < 120 ? 0.050 : 0.200;
		Slower.AddPacket(I * 100, I * 0.1 + Transit);
		Slower.AddFrame(I * 100);
	}
	CHECK(Near(Slower.SenderTimeAt(40.0, 0.0), 40.0 - 0.2 - 0.1));
}

static void TestMatchTimeAt()
{
	// Streamed at 2x: 0.1 s of sending covers 0.2 s of match.
	const std::vector<double> Sent = {0.0, 0.1, 0.2};
	const std::vector<double> Match = {10.0, 10.2, 10.4};
	CHECK(Near(MatchTimeAt(Sent, Match, 0.05), 10.1));
	CHECK(Near(MatchTimeAt(Sent, Match, 0.15), 10.3));
	CHECK(Near(MatchTimeAt(Sent, Match, 5.0), 10.4));   // nothing newer yet: hold the last frame
	CHECK(Near(MatchTimeAt(Sent, Match, -1.0), 10.0));
	CHECK(MatchTimeAt({}, {}, 1.0) == 0.0);
}

static void TestStreamReceiver()
{
	StreamReceiver Receiver;
	Packet P;
	auto Receive = [&Receiver, &P](const std::vector<std::uint8_t>& Bytes, double Arrival) {
		return Receiver.Receive(Bytes.data(), Bytes.size(), Arrival, P);
	};

	// Frames mean nothing until the match info that says whose cars they are.
	CHECK(Receive(FramePacket(7, 1, 100, 0.1f), 0.00) == Verdict::Ignored);
	CHECK(!Receiver.IsFollowing());
	CHECK(Receive(HelloPacket(7, 0, 0), 0.01) == Verdict::NewStream);
	CHECK(Receiver.IsFollowing() && Receiver.GetStream() == 7);

	CHECK(Receive(FramePacket(7, 1, 100, 0.1f), 0.11) == Verdict::Use);
	CHECK(Receive(FramePacket(7, 1, 100, 0.1f), 0.12) == Verdict::Duplicate);
	CHECK(Receive(FramePacket(7, 3, 300, 0.3f), 0.31) == Verdict::Use);
	CHECK(Receive(FramePacket(7, 2, 200, 0.2f), 0.32) == Verdict::Stale);  // overtaken by frame 3
	CHECK(Receiver.StaleFrames == 1);

	CHECK(Receive(EventPacket(7, 4, 5), 0.40) == Verdict::Use);
	CHECK(Receive(EventPacket(7, 5, 5), 0.45) == Verdict::Repeat);  // a second copy of event 5

	// Another sender's match info doesn't interrupt a live stream...
	CHECK(Receive(HelloPacket(8, 0, 0), 0.50) == Verdict::Ignored);
	CHECK(Receiver.GetStream() == 7);
	const std::vector<std::uint8_t> Garbage = {1, 2, 3};
	CHECK(Receive(Garbage, 0.55) == Verdict::Malformed);

	const LinkStats& Link = Receiver.GetLink();
	CHECK(Link.Received == 6 && Link.Duplicates == 1 && Link.Reordered == 1 && Link.Lost() == 0);

	// ...but once it says bye, the next stream starts afresh.
	CHECK(Receive(PacketWriter(PacketType::Bye, 7, 6, 600).Bytes, 0.60) == Verdict::Use);
	CHECK(Receiver.HasEnded());
	CHECK(Receive(HelloPacket(8, 0, 0), 0.70) == Verdict::NewStream);
	CHECK(Receiver.GetStream() == 8 && !Receiver.HasEnded() && Receiver.GetLink().Received == 1);
	CHECK(Receiver.StaleFrames == 0 && Receiver.Ignored == 2 && Receiver.Malformed == 1);

	// A sender that restarts without a bye takes over once the old stream goes quiet.
	CHECK(Receive(HelloPacket(9, 0, 0), 0.70 + StreamTimeout - 0.1) == Verdict::Ignored);
	CHECK(!Receiver.IsQuiet(0.70 + StreamTimeout - 0.1) && Receiver.IsQuiet(0.70 + StreamTimeout + 0.1));
	CHECK(Receive(HelloPacket(9, 0, 0), 0.70 + StreamTimeout + 0.1) == Verdict::NewStream);
	CHECK(Receiver.GetStream() == 9);
}

int main()
{
	return RunTests({
		{"GoldenFrame", TestGoldenFrame},
		{"JsonPackets", TestJsonPackets},
		{"RejectsBadPackets", TestRejectsBadPackets},
		{"LinkStats", TestLinkStats},
		{"Jitter", TestJitter},
		{"PlayoutBuffer", TestPlayoutBuffer},
		{"MatchTimeAt", TestMatchTimeAt},
		{"StreamReceiver", TestStreamReceiver},
	});
}
