"""Static structure checks for `infra/template.yaml` (ADR-0007): schedule
split (sync/notify, both ENABLED, RetryPolicy) + observability (SNS +
CloudWatch Alarms + AlarmEmail parameter). Parsed with a CloudFormation
intrinsic-tag-aware YAML loader so `!Ref`/`!Sub`/`!GetAtt` don't blow up
plain `yaml.safe_load`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "infra" / "template.yaml"


def _cfn_multi_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node):
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node)}
    if isinstance(node, yaml.MappingNode):
        return {tag_suffix: loader.construct_mapping(node)}
    raise TypeError(f"unexpected node type for CloudFormation tag !{tag_suffix}")


class _CfnLoader(yaml.SafeLoader):
    pass


_CfnLoader.add_multi_constructor("!", _cfn_multi_constructor)


def _load_template() -> dict:
    with open(TEMPLATE_PATH) as f:
        return yaml.load(f, Loader=_CfnLoader)


TEMPLATE = _load_template()


def test_alarm_email_parameter_exists():
    assert "AlarmEmail" in TEMPLATE["Parameters"]
    assert TEMPLATE["Parameters"]["AlarmEmail"]["Type"] == "String"


def test_scheduler_events_split_into_sync_and_notify_both_enabled():
    events = TEMPLATE["Resources"]["SchedulerFunction"]["Properties"]["Events"]

    assert "Daily" not in events
    assert set(events) == {"SyncSchedule", "NotifySchedule"}

    sync = events["SyncSchedule"]["Properties"]
    assert events["SyncSchedule"]["Type"] == "ScheduleV2"
    assert sync["ScheduleExpression"] == "cron(0 8 * * ? *)"
    assert sync["ScheduleExpressionTimezone"] == "Asia/Taipei"
    assert sync["State"] == "ENABLED"
    assert '"mode": "sync"' in sync["Input"]

    notify = events["NotifySchedule"]["Properties"]
    assert events["NotifySchedule"]["Type"] == "ScheduleV2"
    assert notify["ScheduleExpression"] == "cron(0 10 * * ? *)"
    assert notify["ScheduleExpressionTimezone"] == "Asia/Taipei"
    assert notify["State"] == "ENABLED"
    assert '"mode": "notify"' in notify["Input"]


def test_scheduler_events_have_retry_policy():
    events = TEMPLATE["Resources"]["SchedulerFunction"]["Properties"]["Events"]

    for name in ("SyncSchedule", "NotifySchedule"):
        retry = events[name]["Properties"]["RetryPolicy"]
        assert retry["MaximumRetryAttempts"] == 2
        assert retry["MaximumEventAgeInSeconds"] == 3600


def test_no_disabled_state_remains_on_scheduler_function():
    events = TEMPLATE["Resources"]["SchedulerFunction"]["Properties"]["Events"]

    for event in events.values():
        assert event["Properties"]["State"] != "DISABLED"


def test_pipeline_alarms_topic_and_email_subscription():
    resources = TEMPLATE["Resources"]

    assert resources["PipelineAlarmsTopic"]["Type"] == "AWS::SNS::Topic"

    subscription = resources["AlarmEmailSubscription"]
    assert subscription["Type"] == "AWS::SNS::Subscription"
    props = subscription["Properties"]
    assert props["Protocol"] == "email"
    assert props["Endpoint"] == {"Ref": "AlarmEmail"}
    assert props["TopicArn"] == {"Ref": "PipelineAlarmsTopic"}


def test_scheduler_and_processor_errors_alarms():
    resources = TEMPLATE["Resources"]

    for alarm_name, function_ref in [
        ("SchedulerErrorsAlarm", "SchedulerFunction"),
        ("ProcessorErrorsAlarm", "ProcessorFunction"),
    ]:
        alarm = resources[alarm_name]
        assert alarm["Type"] == "AWS::CloudWatch::Alarm"
        props = alarm["Properties"]
        assert props["Namespace"] == "AWS/Lambda"
        assert props["MetricName"] == "Errors"
        assert props["Statistic"] == "Sum"
        assert props["Threshold"] == 1
        assert props["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
        dimension = props["Dimensions"][0]
        assert dimension["Name"] == "FunctionName"
        assert dimension["Value"] == {"Ref": function_ref}
        assert props["AlarmActions"] == [{"Ref": "PipelineAlarmsTopic"}]


def test_scheduler_duration_alarm():
    alarm = TEMPLATE["Resources"]["SchedulerDurationAlarm"]

    assert alarm["Type"] == "AWS::CloudWatch::Alarm"
    props = alarm["Properties"]
    assert props["Namespace"] == "AWS/Lambda"
    assert props["MetricName"] == "Duration"
    assert props["Statistic"] == "Maximum"
    assert props["Threshold"] == 250000
    assert props["ComparisonOperator"] == "GreaterThanOrEqualToThreshold"
    assert props["Dimensions"][0]["Value"] == {"Ref": "SchedulerFunction"}
    assert props["AlarmActions"] == [{"Ref": "PipelineAlarmsTopic"}]
