"""gdb script: summarize one core for the C++ fleet shutdown diagnosis (diagnostic branch only).

Usage: gdb -batch -nx -x tests/diag_gdb_inspect.py EXECUTABLE CORE

Prints the fault, the loaded libraries, every thread's full backtrace, a compact stack per
thread, the crashing thread's frames in the Fast DDS reader and discovery path with `this`,
arguments, and locals, and every frame in any thread that belongs to a teardown path. Values
gdb cannot recover are printed as such; this script infers nothing.
"""

import re

import gdb

FOCUS = re.compile(
    r'StatefulReader::|RTPSReader::|WriterProxy::|EDP::unpairWriterProxy'
    r'|PDP::removeWriterProxyData|EDPSimplePUBListener::|EDPSimpleSUBListener::')
TEARDOWN = re.compile(
    r'~StatefulReader|~RTPSReader|~DataReader|~Subscription|~Publisher|~Node|~LifecycleNode'
    r'|~Executor|~Context|~DomainParticipant|~RTPSParticipantImpl|~PDP|~EDP'
    r'|deleteUserEndpoint|delete_datareader|delete_subscriber|delete_participant'
    r'|delete_contained_entities|removeReader|unregisterReader'
    r'|rmw_destroy_subscription|rmw_destroy_node|rmw_context_fini|rmw_shutdown'
    r'|rcl_subscription_fini|rcl_node_fini|rcl_shutdown|rcl_context_fini'
    r'|Context::shutdown|rclcpp::shutdown|SignalHandler|signal_handler'
    r'|__run_exit_handlers|_dl_fini')
EXPRESSIONS = ('this', '*this', 'this->m_guid', 'this->is_alive_', 'writer_guid')


def run(command):
    try:
        return gdb.execute(command, to_string=True)
    except gdb.error as exc:
        return f'<{command}: {exc}>\n'


def section(title, text):
    print(f'=== {title} ===')
    print(text.rstrip(), flush=True)


def stack(thread):
    thread.switch()
    frames = []
    try:
        frame = gdb.newest_frame()
    except gdb.error:
        return frames
    while frame is not None and len(frames) < 200:
        try:
            name = frame.name() or f'?? @ {frame.pc():#x}'
        except gdb.error:
            name = '??'
        frames.append((frame, name))
        try:
            frame = frame.older()
        except gdb.error:
            break
    return frames


def main():
    for setting in ('pagination off', 'confirm off', 'print pretty on', 'print object on',
                    'print elements 64', 'print max-depth 4'):
        run('set ' + setting)
    inferior = gdb.selected_inferior()
    crashing = gdb.selected_thread()
    section('fault', ''.join(run('p ' + expression) for expression in (
        '$_siginfo.si_signo', '$_siginfo.si_code', '$_siginfo._sifields._sigfault.si_addr')))
    section('shared libraries', run('info sharedlibrary'))
    section('threads', run('info threads'))
    section('all threads: bt full', run('thread apply all bt full'))

    print('=== compact stacks (newest frame first) ===')
    teardown = []
    for thread in sorted(inferior.threads(), key=lambda value: value.num):
        names = [name for _, name in stack(thread)]
        lwp = thread.ptid[1]
        roles = [role for role, present in (('crashing', thread.num == crashing.num),
                                            ('main', lwp == inferior.pid)) if present]
        print(f'Thread {thread.num} LWP {lwp} [{",".join(roles) or "-"}]: '
              + ' <- '.join(names[:40]))
        if thread.num == crashing.num:
            print('CRASH_STACK: ' + ' <- '.join(names[:40]))
        teardown.extend(f'thread {thread.num} LWP {lwp} #{index} {name}'
                        for index, name in enumerate(names) if TEARDOWN.search(name))
    section('teardown frames in any thread', '\n'.join(teardown) or 'none')
    for line in teardown:
        print('TEARDOWN_FRAME: ' + line)

    for index, (frame, name) in enumerate(stack(crashing)):
        if not FOCUS.search(name):
            continue
        frame.select()
        details = [run('info frame'), run('info args'), run('info locals')]
        details.extend(f'p {expression}\n' + run('p ' + expression) for expression in EXPRESSIONS)
        section(f'crashing thread frame #{index}: {name}', ''.join(details))
    print('[diag_gdb_inspect] done', flush=True)


main()
