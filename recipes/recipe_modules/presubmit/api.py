# Copyright 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from recipe_engine import recipe_api

from PB.recipe_engine import result as result_pb2
from PB.go.chromium.org.luci.buildbucket.proto import common as common_pb2


class PresubmitApi(recipe_api.RecipeApi):

  def __init__(self, properties, **kwargs):
    super(PresubmitApi, self).__init__(**kwargs)

    self._clear_pythonpath = properties.get('clear_pythonpath', False)
    self._runhooks = properties.get('runhooks', False)
    # 8 minutes seems like a reasonable upper bound on presubmit timings.
    # According to event mon data we have, it seems like anything longer than
    # this is a bug, and should just instant fail.
    self._timeout_s = properties.get('timeout_s', 480)
    self._vpython_spec_path = properties.get('vpython_spec_path')

  @property
  def presubmit_support_path(self):
    return self.repo_resource('presubmit_support.py')

  def __call__(self, *args, **kwargs):
    """Return a presubmit step."""

    name = kwargs.pop('name', 'presubmit')
    with self.m.depot_tools.on_path():
      presubmit_args = list(args) + [
          '--json_output', self.m.json.output(),
      ]
      step_data = self.m.python(
          name, self.presubmit_support_path, presubmit_args, **kwargs)
      return step_data.json.output

  def prepare(self):
    """Set up a presubmit run."""
    # Expect callers to have already set up their gclient configuration.

    bot_update_step = self.m.bot_update.ensure_checkout()
    relative_root = self.m.gclient.get_gerrit_patch_root().rstrip('/')

    abs_root = self.m.context.cwd.join(relative_root)
    with self.m.context(cwd=abs_root):
      # TODO(unowned): Consider extracting user name & email address from
      # the issue rather than using the commit bot.
      self.m.git('-c', 'user.email=commit-bot@chromium.org',
              '-c', 'user.name=The Commit Bot',
              'commit', '-a', '-m', 'Committed patch',
              name='commit-git-patch', infra_step=False)

    if self._runhooks:
      with self.m.context(cwd=self.m.path['checkout']):
        self.m.gclient.runhooks()

    return bot_update_step

  def execute(self, bot_update_step):
    relative_root = self.m.gclient.get_gerrit_patch_root().rstrip('/')
    abs_root = self.m.context.cwd.join(relative_root)
    got_revision_properties = self.m.bot_update.get_project_revision_properties(
        # Replace path.sep with '/', since most recipes are written assuming '/'
        # as the delimiter. This breaks on windows otherwise.
        relative_root.replace(self.m.path.sep, '/'), self.m.gclient.c)
    upstream = bot_update_step.json.output['properties'].get(
        got_revision_properties[0])

    presubmit_args = [
      '--issue', self.m.tryserver.gerrit_change.change,
      '--patchset', self.m.tryserver.gerrit_change.patchset,
      '--gerrit_url', 'https://%s' % self.m.tryserver.gerrit_change.host,
      '--gerrit_fetch',
    ]
    if self.m.cq.state == self.m.cq.DRY:
      presubmit_args.append('--dry_run')

    presubmit_args.extend([
      '--root', abs_root,
      '--commit',
      '--verbose', '--verbose',
      '--skip_canned', 'CheckTreeIsOpen',
      '--skip_canned', 'CheckBuildbotPendingBuilds',
      '--upstream', upstream,  # '' if not in bot_update mode.
    ])

    env = {}
    if self._clear_pythonpath:
      # This should overwrite the existing pythonpath which includes references to
      # the local build checkout (but the presubmit scripts should only pick up
      # the scripts from presubmit_build checkout).
      env['PYTHONPATH'] = ''

    venv = None
    if self._vpython_spec_path:
      venv = abs_root.join(self._vpython_spec_path)

    raw_result = result_pb2.RawResult()
    with self.m.context(env=env):
      step_json = self(
          *presubmit_args,
          venv=venv, timeout=self._timeout_s,
          # ok_ret='any' causes all exceptions to be ignored in this step
          ok_ret='any')
      # Set recipe result values
      if step_json:
        raw_result.summary_markdown = _createSummaryMarkdown(step_json)

      retcode = self.m.step.active_result.retcode
      if retcode == 0:
        raw_result.status = common_pb2.SUCCESS
        return raw_result

      self.m.step.active_result.presentation.status = 'FAILURE'
      if self.m.step.active_result.exc_result.had_timeout:
        # TODO(iannucci): Shouldn't we also mark failure on timeouts?
        raw_result.summary_markdown += (
            '\n\nTimeout occurred during presubmit step.')
      if retcode == 1:
        raw_result.status = common_pb2.FAILURE
        self.m.tryserver.set_test_failure_tryjob_result()
      else:
        raw_result.status = common_pb2.INFRA_FAILURE
        self.m.tryserver.set_invalid_test_results_tryjob_result()
      # Handle unexpected errors not caught by json output
      if raw_result.summary_markdown == '':
        raw_result.status = common_pb2.INFRA_FAILURE
        raw_result.summary_markdown = (
          'Something unexpected occurred'
          ' while running presubmit checks.'
          ' Please [file a bug](https://bugs.chromium.org'
          '/p/chromium/issues/entry?components='
          'Infra%3EClient%3EChrome&status=Untriaged)'
        )
    return raw_result


def _limitSize(message_list, char_limit=450):
  """Returns a list of strings within a certain character length.

  Args:
     * message_list (List[str]) - The message to truncate as a list
       of lines (without line endings).
  """
  hint = ('**The complete output can be'
          ' found at the bottom of the presubmit stdout.**')
  char_count = 0
  for index, message in enumerate(message_list):
    char_count += len(message)
    if char_count > char_limit:
      total_errors = len(message_list)
      oversized_msg = ('**Error size > %d chars, '
      'there are %d more error(s) (%d total)**') % (
        char_limit, total_errors - index, total_errors
      )
      if index == 0:
        # Show at minimum part of the first error message
        first_message = message_list[index].replace('\n\n', '\n')
        return ['\n\n'.join(
          _limitSize(first_message.splitlines())
          )
        ]
      return message_list[:index] + [oversized_msg, hint]
  return message_list


def _createSummaryMarkdown(step_json):
  """Returns a string with data on errors, warnings, and notifications.

  Extracts the number of errors, warnings and notifications
  from the dictionary(step_json).

  Then it lists all the errors line by line.

  Args:
      * step_json = {
        'errors': [
          {
            'message': string,
            'long_text': string,
            'items: [string],
            'fatal': boolean
          }
        ],
        'notifications': [
          {
            'message': string,
            'long_text': string,
            'items: [string],
            'fatal': boolean
          }
        ],
        'warnings': [
          {
            'message': string,
            'long_text': string,
            'items: [string],
            'fatal': boolean
          }
        ]
      }
  """
  errors = step_json['errors']
  warning_count = len(step_json['warnings'])
  notif_count = len(step_json['notifications'])
  description = (
    'There are %d error(s), %d warning(s),'
    ' and %d notifications(s). Here are the errors:') % (
      len(errors), warning_count, notif_count
  )
  error_messages = []

  for error in errors:
    # markdown represents new lines with 2 spaces
    # replacing the \n with \n\n because \n gets replaced with an empty space.
    # This way it will work on both markdown and plain text.
    error_messages.append(
      '**ERROR**\n\n%s\n\n%s' % (
      error['message'].replace('\n', '\n\n'),
      error['long_text'].replace('\n', '\n\n'))
    )

  error_messages = _limitSize(error_messages)
  # Description is not counted in the total message size.
  # It is inserted afterward to ensure it is the first message seen.
  error_messages.insert(0, description)
  if warning_count or notif_count:
    error_messages.append(
      ('To see notifications and warnings,'
      ' look at the stdout of the presubmit step.')
    )
  return '\n\n'.join(error_messages)

