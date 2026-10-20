// Diagnostic LD_PRELOAD shim for runtime tests only; never part of a generated package.
//
// On SIGSEGV, SIGBUS, or SIGABRT it writes a best-effort backtrace to stderr and then
// re-raises the same signal with the default action, so the process still dies from
// the original signal and its exit status is unchanged. backtrace() is not
// async-signal-safe: the output is a hint for investigation, not proof of a cause.
// A process that installs its own handler for these signals replaces this one.

#define _GNU_SOURCE
#include <execinfo.h>
#include <signal.h>
#include <string.h>
#include <unistd.h>

static void report(int sig)
{
  static const char header[] = "\n[segv_backtrace] fatal signal; best-effort backtrace:\n";
  void * frames[64];
  ssize_t ignored = write(STDERR_FILENO, header, sizeof(header) - 1);
  (void)ignored;
  int count = backtrace(frames, 64);
  backtrace_symbols_fd(frames, count, STDERR_FILENO);
  // SA_RESETHAND restored the default action; SA_NODEFER lets it take effect now.
  raise(sig);
}

__attribute__((constructor)) static void install(void)
{
  // Load the unwinder before any fault, so the handler does not have to.
  void * warmup[1];
  backtrace(warmup, 1);

  struct sigaction action;
  memset(&action, 0, sizeof(action));
  action.sa_handler = report;
  action.sa_flags = SA_RESETHAND | SA_NODEFER;
  sigemptyset(&action.sa_mask);
  sigaction(SIGSEGV, &action, NULL);
  sigaction(SIGBUS, &action, NULL);
  sigaction(SIGABRT, &action, NULL);
}
