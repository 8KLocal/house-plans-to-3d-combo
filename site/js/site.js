(function () {
  "use strict";

  var nav = document.getElementById("site-nav");
  var navToggle = document.querySelector("[data-nav-toggle]");
  var desktop = window.matchMedia("(min-width: 860px)");

  function setNav(open) {
    if (!nav || !navToggle) {
      return;
    }
    nav.classList.toggle("is-open", open);
    navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    navToggle.textContent = open ? "Close" : "Menu";
  }

  if (navToggle) {
    navToggle.addEventListener("click", function () {
      setNav(navToggle.getAttribute("aria-expanded") !== "true");
    });
  }

  if (nav) {
    nav.addEventListener("click", function (event) {
      if (event.target.closest("a")) {
        setNav(false);
      }
    });
  }

  desktop.addEventListener("change", function () {
    if (desktop.matches) {
      setNav(false);
    }
  });

  var chat = document.getElementById("chat");
  var panel = document.getElementById("chat-panel");
  var chatToggle = document.querySelector("[data-chat-toggle]");
  var chatClose = document.querySelector("[data-chat-close]");

  function setChat(open) {
    if (!panel || !chatToggle) {
      return;
    }
    panel.hidden = !open;
    chatToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      var closeButton = chatClose;
      if (closeButton) {
        closeButton.focus();
      }
    } else {
      chatToggle.focus();
    }
  }

  if (chatToggle) {
    chatToggle.addEventListener("click", function () {
      setChat(panel.hidden);
    });
  }

  if (chatClose) {
    chatClose.addEventListener("click", function () {
      setChat(false);
    });
  }

  if (panel) {
    panel.addEventListener("click", function (event) {
      var button = event.target.closest("[data-qa]");
      if (!button) {
        return;
      }
      var answer = document.getElementById(button.getAttribute("aria-controls"));
      if (!answer) {
        return;
      }
      var open = button.getAttribute("aria-expanded") === "true";
      button.setAttribute("aria-expanded", open ? "false" : "true");
      answer.hidden = open;
    });
  }

  document.addEventListener("click", function (event) {
    if (!panel || panel.hidden || !chat) {
      return;
    }
    if (!chat.contains(event.target)) {
      setChat(false);
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    if (panel && !panel.hidden) {
      setChat(false);
    } else if (navToggle && navToggle.getAttribute("aria-expanded") === "true") {
      setNav(false);
      navToggle.focus();
    }
  });

  var form = document.querySelector("[data-order-form]");
  if (!form) {
    return;
  }

  var pickup = form.querySelector("#pickup");
  var address = document.getElementById("ship-address");
  var addressInputs = form.querySelectorAll("[data-address]");
  var packageRadios = form.querySelectorAll('input[name="Package"]');

  function syncAddress() {
    var shipping = !(pickup && pickup.checked);
    if (address) {
      address.hidden = !shipping;
    }
    Array.prototype.forEach.call(addressInputs, function (input) {
      input.required = shipping;
    });
  }

  if (pickup) {
    pickup.addEventListener("change", syncAddress);
    syncAddress();
  }

  var floorsChoice = form.querySelector("#floors-include");
  var floorsSpecifyWrap = document.getElementById("floors-specify-wrap");
  var floorsSpecify = document.getElementById("floors-specify");
  var roofStyle = form.querySelector("#roof-style");
  var roofOtherWrap = document.getElementById("roof-other-wrap");
  var roofOther = document.getElementById("roof-other");

  function syncFloors() {
    var specify = floorsChoice && floorsChoice.value === "Specify";
    if (floorsSpecifyWrap) {
      floorsSpecifyWrap.hidden = !specify;
    }
    if (floorsSpecify) {
      floorsSpecify.required = !!specify;
    }
  }

  function groupVisible(id) {
    var group = document.getElementById(id);
    return group && !group.hidden;
  }

  function syncRoof() {
    var other = groupVisible("exterior-questions") && roofStyle && roofStyle.value === "Other";
    if (roofOtherWrap) {
      roofOtherWrap.hidden = !other;
    }
    if (roofOther) {
      roofOther.required = !!other;
    }
  }

  function applyGroup(id, show) {
    var group = document.getElementById(id);
    if (!group) {
      return;
    }
    group.hidden = !show;
    Array.prototype.forEach.call(group.querySelectorAll("[data-required]"), function (el) {
      el.required = !!show;
    });
    if (!show) {
      Array.prototype.forEach.call(group.querySelectorAll("input, select, textarea"), function (el) {
        el.required = false;
      });
    }
  }

  function syncModel() {
    var picked = form.querySelector('input[name="Model type"]:checked');
    var value = picked ? picked.value : "";
    applyGroup("exterior-questions", value === "Exterior only" || value === "Both");
    applyGroup("interior-questions", value === "Interior only" || value === "Both");
    syncRoof();
  }

  if (floorsChoice) {
    floorsChoice.addEventListener("change", syncFloors);
    syncFloors();
  }

  if (roofStyle) {
    roofStyle.addEventListener("change", syncRoof);
  }

  Array.prototype.forEach.call(form.querySelectorAll('input[name="Model type"]'), function (radio) {
    radio.addEventListener("change", syncModel);
  });
  syncModel();

  var plateYes = form.querySelector('input[name="Name or address plate"][value="Yes"]');
  var plateWrap = document.getElementById("plate-text-wrap");
  var plateText = document.getElementById("plate-text");
  var baseShape = form.querySelector("#base-shape");
  var baseOtherWrap = document.getElementById("base-other-wrap");
  var baseOther = document.getElementById("base-other");

  function syncPlate() {
    var show = plateYes && plateYes.checked;
    if (plateWrap) {
      plateWrap.hidden = !show;
    }
    if (plateText) {
      plateText.required = !!show;
    }
  }

  function syncBase() {
    var other = baseShape && baseShape.value === "Other";
    if (baseOtherWrap) {
      baseOtherWrap.hidden = !other;
    }
    if (baseOther) {
      baseOther.required = !!other;
    }
  }

  Array.prototype.forEach.call(form.querySelectorAll('input[name="Name or address plate"]'), function (radio) {
    radio.addEventListener("change", syncPlate);
  });
  syncPlate();

  if (baseShape) {
    baseShape.addEventListener("change", syncBase);
    syncBase();
  }

  var requested = new URLSearchParams(window.location.search).get("package");
  if (requested) {
    var wanted = requested.toLowerCase();
    Array.prototype.forEach.call(packageRadios, function (radio) {
      if (radio.value.toLowerCase().indexOf(wanted) === 0) {
        radio.checked = true;
      }
    });
  }

  form.addEventListener("submit", function () {
    var submitButton = form.querySelector("[type='submit']");
    window.setTimeout(function () {
      if (submitButton) {
        submitButton.disabled = true;
        submitButton.textContent = "Sending…";
      }
    }, 0);
  });
})();
