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
