(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  if (root) root.ConsoleLauncher = value;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";
  function profileLabel(profile) {
    return (
      profile?.label || profile?.player_name || profile?.name || "未选择存档"
    );
  }
  function activeProfileId(launcher) {
    return launcher?.running
      ? Number(
          launcher.profile_id ||
            launcher.active_profile_id ||
            launcher.running_profile_id ||
            0,
        ) || null
      : null;
  }
  function initialProfile(profiles, launcher, remembered) {
    const online = profiles.filter((profile) => profile.online);
    const ids = [
      activeProfileId(launcher),
      online.length === 1 ? online[0].id : null,
      Number(remembered),
      Number(launcher?.configured_profile_id),
    ];
    for (const id of ids)
      if (id && profiles.some((profile) => profile.id === id)) return id;
    return (
      profiles.find((profile) => profile.online)?.id ||
      profiles.find((profile) => profile.has_state)?.id ||
      profiles[0]?.id ||
      null
    );
  }
  function launchState(launcher, selected, ready) {
    if (!launcher)
      return {
        disabled: true,
        label: "读取启动状态…",
        message: "正在连接启动器",
      };
    if (launcher.running) {
      const id = activeProfileId(launcher);
      return {
        disabled: true,
        label: "游戏运行中",
        message:
          id === selected
            ? "游戏正在使用当前存档"
            : "游戏使用另一份存档；切换游戏存档前请先退出游戏",
      };
    }
    if (!selected || !ready)
      return {
        disabled: true,
        label: "启动游戏",
        message: "请先选择已初始化的存档，或创建新存档",
      };
    if (launcher.available === false || launcher.can_start === false)
      return {
        disabled: true,
        label: "暂不能启动",
        message: launcher.reason || "请检查本地客户端配置",
      };
    return {
      disabled: false,
      label: "启动游戏",
      message: "将使用当前选中的存档启动",
    };
  }
  return { profileLabel, activeProfileId, initialProfile, launchState };
});
