from app.schemas import ProjectIn


def test_campaign_mode_separate_is_default():
    project = ProjectIn(name="Example project", domain="example.ru")
    assert project.campaign_mode == "separate"
    assert project.domain == "https://example.ru"


def test_single_epk_mode_is_available():
    project = ProjectIn(name="Example project", domain="https://example.ru", campaign_mode="single_epk")
    assert project.campaign_mode == "single_epk"


def test_ids_are_deduplicated():
    project = ProjectIn(name="Example project", domain="example.ru", account_ids=[123, 123, 456], region_ids=[213, 213])
    assert project.account_ids == [123, 456]
    assert project.region_ids == [213]


def test_image_urls_must_be_absolute_urls():
    try:
        ProjectIn(name="Example project", domain="example.ru", image_urls=["/banner.png"])
    except ValueError:
        return
    raise AssertionError("Relative image URLs must be rejected")
