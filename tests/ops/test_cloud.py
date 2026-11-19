"""Cloud inventory: read-only probes and JSON parsers."""

from free_claude_code.ops import (
    aws_instances_probe,
    aws_security_groups_probe,
    azure_vms_probe,
    gcloud_instances_probe,
    not_running,
    parse_aws_instances,
    parse_azure_vms,
    parse_gcloud_instances,
)
from free_claude_code.terminal.policy import RiskLevel, classify

AWS_INSTANCES = {
    "Reservations": [
        {
            "Instances": [
                {
                    "InstanceId": "i-0abc",
                    "InstanceType": "t3.micro",
                    "State": {"Name": "running"},
                    "Placement": {"AvailabilityZone": "ap-south-1a"},
                    "Tags": [{"Key": "Name", "Value": "web-1"}],
                },
                {
                    "InstanceId": "i-0def",
                    "InstanceType": "t3.small",
                    "State": {"Name": "stopped"},
                    "Placement": {"AvailabilityZone": "ap-south-1b"},
                    "Tags": [],
                },
            ]
        }
    ]
}

GCLOUD_INSTANCES = [
    {
        "id": "123456",
        "name": "api-1",
        "status": "RUNNING",
        "zone": "https://www.googleapis.com/compute/v1/projects/p/zones/asia-south1-a",
        "machineType": "https://www.googleapis.com/compute/v1/projects/p/zones/asia-south1-a/machineTypes/e2-small",
    },
    {
        "id": "123457",
        "name": "api-2",
        "status": "TERMINATED",
        "zone": "asia-south1-b",
        "machineType": "e2-medium",
    },
]

AZURE_VMS = [
    {
        "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm1",
        "name": "vm1",
        "powerState": "VM running",
        "hardwareProfile": {"vmSize": "Standard_B2s"},
        "location": "southindia",
    },
    {
        "id": "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.Compute/virtualMachines/vm2",
        "name": "vm2",
        "powerState": "VM deallocated",
        "hardwareProfile": {"vmSize": "Standard_B1s"},
        "location": "southindia",
    },
]


def test_inventory_probes_are_read_only() -> None:
    for probe in (
        aws_instances_probe(),
        aws_security_groups_probe(),
        gcloud_instances_probe(),
        azure_vms_probe(),
    ):
        level, rule = classify(probe.argv)
        assert level is RiskLevel.R0_READ_ONLY, (probe.argv, rule)


def test_parse_aws_instances() -> None:
    instances = parse_aws_instances(AWS_INSTANCES)
    assert len(instances) == 2
    assert instances[0].name == "web-1"
    assert instances[0].state == "running"
    assert instances[0].zone == "ap-south-1a"
    assert instances[1].name == ""


def test_parse_gcloud_instances_strips_url_prefixes() -> None:
    instances = parse_gcloud_instances(GCLOUD_INSTANCES)
    assert instances[0].zone == "asia-south1-a"
    assert instances[0].type == "e2-small"
    assert instances[1].zone == "asia-south1-b"
    assert instances[1].type == "e2-medium"


def test_parse_azure_vms_normalizes_power_state() -> None:
    vms = parse_azure_vms(AZURE_VMS)
    assert vms[0].state == "running"
    assert vms[1].state == "deallocated"


def test_not_running_flags_each_provider() -> None:
    all_instances = (
        parse_aws_instances(AWS_INSTANCES)
        + parse_gcloud_instances(GCLOUD_INSTANCES)
        + parse_azure_vms(AZURE_VMS)
    )
    stopped = not_running(all_instances)
    assert [i.name for i in stopped] == ["", "api-2", "vm2"]
